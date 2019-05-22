#!/usr/bin/env python
import sys, os, re, yaml, threading, subprocess, time, logging
from collections import OrderedDict

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from multiprocessing import Process

"""
Main "Site" class for managing a single Wordpress site.
"""
class Site(FileSystemEventHandler):
  backup_timer = None

  # Execute a backup of this site.
  def backup(self):
    num_files = len(self.modified_files)
    self.logger.info(f'[backup] triggered for {num_files} files')
    self.backup_timer = None
    self.modified_files = []

    for mode in self.backup_modes:
      self.logger.info(f'[backup] mode {mode}')
      if mode == 'zip':
        fp = '/usr/local/%s' % self.cfg['fn_zip']
        cmd = ['cd %s &&' % self.cfg['site_dir'], 'zip', '-r']
        cmd += ['-x "%s"' % x for x in self.backup_exclude]
        if self.quiet: cmd.append('-q')
        cmd.append(fp)
        cmd.append('.')
        sh(' '.join(cmd))
        self.s3_copy(True)
        sh('rm -rf "%s"' % fp)
      elif mode == 'sync':
        self.s3_sync(True)
      else:
        self.logger.error(f'unknown backup mode: {mode}')
    self.logger.info('[backup] finished')

  # Sync the zip file up/down
  def s3_copy(self, up):
    if len(self.cfg['s3_bucket']) <= 0: raise Exception('s3_bucket required')
    fp = '/usr/local/%s' % self.cfg['fn_zip']
    s3_fp = "s3://%s/%s" % (self.cfg['s3_bucket'], self.cfg['fn_zip'])
    cmd = ['aws', 's3', 'cp']
    if self.quiet: cmd.append('--quiet')
    if up: cmd += [fp, s3_fp]
    else: cmd += [s3_fp, fp]
    return sh(' '.join(cmd))

  # Sync the local path up/down
  def s3_sync(self, up):
    if len(self.cfg['s3_bucket']) <= 0: raise Exception('s3_bucket required')
    quiet = not self.logger.isEnabledFor(logging.DEBUG)
    s3_path = "s3://%s/%s" % (self.cfg['s3_bucket'], self.cfg['site_name'])
    cmd = ['aws', 's3', 'sync']
    if up: cmd += [self.cfg['site_dir'], s3_path]
    else: cmd += [s3_path, self.cfg['site_dir']]
    cmd += ['--exclude "%s"' % x for x in self.backup_exclude]
    cmd.append('--delete')
    if self.quiet: cmd.append('--quiet')
    return sh(' '.join(cmd))

  # When watchdog receives a modification event, schedule a backup of the site.
  def on_any_event(self, event):
    if os.path.isdir(event.src_path): return
    if event.src_path.endswith('.accessed'): return
    if 'wp-content/temp-write-test' in event.src_path: return
    self.logger.debug('received file modification event for ' + event.src_path)
    self.modified_files.append(event.src_path)
    if self.backup_timer: self.backup_timer.cancel()
    self.backup_timer = threading.Timer(float(self.cfg['backup_delay']), self.backup)
    self.backup_timer.start()

  # Use a templated config file and replace the variables, then append it to the destination file.
  def append_template_config(self, name, dst_fn):
    src_fn = "/usr/local/etc/templates/%s.conf.template" % name
    with open(src_fn, 'r') as src, open(dst_fn, 'a') as dst:
      cfg = src.read()
      for var_name in self.cfg:
        if self.cfg[var_name] and type(self.cfg[var_name]) is str:
          cfg = cfg.replace("{{%s}}" % var_name.upper(), self.cfg[var_name])
      self.logger.debug(f'writing to {dst_fn}: {cfg}')
      dst.write(cfg)

  # Set up nginx & FPM for this site.
  def configure(self):
    self.logger.debug('configuring nginx, fpm, and docker')
    nginx_cfg = OrderedDict({
      'listen': self.cfg['server_port'],
      'server_name': self.cfg['server_name'],
      'root': self.cfg['site_dir'],
      'index': 'index.php',
      'access_log': self.cfg['nginx_access_log'],
      'error_log': self.cfg['nginx_error_log'],
    })
    nginx_cfg = [f"    {k} {nginx_cfg[k]};" for k in nginx_cfg if len(nginx_cfg[k]) > 0]
    self.cfg['nginx_server_config'] = "\n".join(nginx_cfg)
    self.append_template_config("nginx", "/etc/nginx/conf.d/%s.conf" % self.cfg['site_name'])
    self.append_template_config("fpm", "/usr/local/etc/php-fpm.d/%s.conf" % self.cfg['site_name'])
    self.append_template_config("docker", "/usr/local/etc/php-fpm.d/docker.conf")
    self.append_template_config("zz-docker", "/usr/local/etc/php-fpm.d/zz-docker.conf")

  # Download any backups for this site
  def restore(self):
    mode = self.cfg['restore_mode'].lower()
    policy = self.cfg['restore_policy'].lower()
    if policy == 'missing':
      if os.path.isdir(self.cfg['site_dir']):
        self.logger.info('skipping restoration because the directory was already present')
        return
    elif policy == 'never':
      self.logger.info('skipping restoration because policy=never')
      return
    elif policy != 'always':
      raise Exception(f'invalid restore policy {policy}')

    self.logger.info(f'restoring via {policy} {mode}')
    if mode == 'zip':
      fp = '/usr/local/%s' % self.cfg['fn_zip']
      ret = self.s3_copy(False)
      self.logger.info('copied zip? %s' % ret)
      if ret == 0:
        cmd = ['unzip']
        if self.quiet: cmd.append('-q')
        cmd.append('-o "%s"' % fp)
        cmd.append('-d "%s"' % self.cfg['site_dir'])
        sh(' '.join(cmd))
    elif mode == 'sync':
      self.s3_sync(False)
    elif len(mode) > 0:
      raise Exception(f'unknown restore mode {mode}')
    sh('mkdir -p %s' % self.cfg['site_dir'])
    sh('chown -Rf www-data.www-data %s' % self.cfg['site_dir'])

  # Install Wordpress (or tweak its settings, if necessary)
  def install(self):
    wp_cfg_fp = "%s/wp-config.php" % self.cfg['site_dir']
    if os.path.isfile(wp_cfg_fp):
      self.logger.debug('configuring wordpress')
      with open(wp_cfg_fp, 'r') as file: wp_cfg = file.read()
      with open(wp_cfg_fp, 'w') as file:
        for var_name in self.cfg:
          if not var_name.startswith('wordpress_') or not self.cfg[var_name]: continue
          wp_name = var_name[len('wordpress_'):].upper()
          self.logger.debug(f'configure wordpress variable {wp_name}')
          val = self.cfg[var_name]
          wp_cfg = re.sub(rf"'{wp_name}',.*'.*'", f"'{wp_name}', '{val}'", wp_cfg)
        file.write(wp_cfg)
    else:
      sh('cd %s && /usr/local/bin/wp-entrypoint.sh php-fpm' % self.cfg['site_dir'])

  def watch_for_backup(self):
    self.logger.info('watching for file changes to trigger backups...')
    self.observer = Observer()
    self.observer.schedule(self, path=self.cfg['site_dir'], recursive=True)
    self.observer.daemon = True
    self.observer.start()

  def __init__(self, site_name, fpm_port):
    # Set up the default config:
    self.cfg = {
      'fpm_port': str(fpm_port),
      'server_port': "80",
      'server_name': '',
      'site_dir': "/var/www/html/%s" % site_name,
      'fn_zip': '%s.zip' % site_name,
      'wordpress_db_host': None,
      'wordpress_db_user': None,
      'wordpress_db_password': None,
      'wordpress_db_name': None,
      'wordpress_table_prefix': None,
    }
    self.cfg.update(default_cfg)
    self.cfg = load_config_vars(self.cfg, '/etc/multipress/sites/%s.yaml' % site_name, site_name)
    self.cfg['site_name'] = site_name
    self.logger = logging.getLogger(site_name)
    self.logger.setLevel(self.cfg['log_level'])

    self.backup_modes = str2list(self.cfg['backup_mode'])
    self.backup_exclude = str2list(self.cfg['backup_exclude'])
    self.quiet = not self.logger.isEnabledFor(logging.DEBUG)
    self.modified_files = []
    self.configure()
    self.restore()
    self.install()

    if len(self.backup_modes) > 0: self.watch_for_backup()
    self.logger.info('succesfully configured')

# Beginning with a dictionary of default values, layer in yaml config values and finally env vars
def load_config_vars(values, yamlfp, context = None):
  if os.path.isfile(yamlfp):
    with open(yamlfp, 'r') as stream:
      values.update(yaml.safe_load(stream))

  for name in values:
    val = os.getenv(name.upper(), None)
    if context: val = os.getenv('%s_%s' % (context.upper(), name.upper()), val)
    if val: values[name] = val

  return values

# Replaces placeholders within a file using the default_cfg (or env var overrides)
def replace_placeholders(fp):
  with open(fp, 'r') as file: cfg = file.read()
  with open(fp, 'w') as file:
    for var_name in default_cfg:
      ev = var_name.upper()
      cfg = cfg.replace("{{%s}}" % ev, os.getenv(ev, default_cfg[var_name]))
    logging.debug(f'writing to {fp}: {cfg}')
    file.write(cfg)

# Execute a shell command
def sh(cmd):
  logging.debug(cmd)
  proc = subprocess.Popen(cmd, shell=True)
  comm = proc.communicate()
  return proc.returncode

def str2list(string):
  if type(string) is list: return string
  return [x.strip() for x in re.split(r'(\s+)', string) if x.strip()]

# Turn the value of an env var into a list of strings, split by whitespace.
def get_env_var_list(name, default = ''):
  return [x.strip() for x in re.split(r'(\s+)', os.getenv(name, default)) if x.strip()]

default_cfg = load_config_vars({
  'log_level': 'INFO',
  'log_format': '[%(asctime)s] [%(process)d] [%(levelname)s] [%(name)s] %(message)s',
  'server_port': "80",
  'nginx_access_log': '/dev/stdout main',
  'nginx_error_log': '/dev/stderr warn',
  'fpm_access_log': '/dev/stdout',
  'fpm_error_log': '/dev/stderr',
  's3_bucket': '',
  'restore_policy': 'missing',
  'restore_mode': '',
  'backup_mode': '',
  'backup_exclude': '*.DS_Store',
  'backup_delay': '30.0',
  # From: https://github.com/nginx/nginx/blob/master/conf/nginx.conf
  'nginx_main_log_format': ('\'$remote_addr - $remote_user [$time_local] "$request" '
                            '$status $body_bytes_sent "$http_referer" '
                            '"$http_user_agent" "$http_x_forwarded_for"\'')
}, '/etc/multipress/config.yaml')
logging.basicConfig(format=default_cfg['log_format'], level=default_cfg['log_level'])

sites = {}

if __name__ == "__main__":
  replace_placeholders('/etc/nginx/nginx.conf')
  replace_placeholders('/usr/local/etc/php-fpm.d/docker.conf')

  fpm_port = 9000
  sitenames = set(str2list(os.getenv('SITES', '')))
  d = '/etc/multipress/sites'
  if os.path.isdir(d):
    sitenames = sitenames.union(set(
      [os.path.splitext(item)[0] for item in os.listdir(d) if os.path.isfile(os.path.join(d, item))]
    ))
  if not len(sitenames) > 0:
    raise Exception("No sites provided via the SITES enviroment variable or yaml config files")
  for site_name in sitenames:
    sites[site_name] = Site(site_name, fpm_port)
    fpm_port += 1
  sh('nginx -g "daemon off;" &')
  sh('exec php-fpm')
