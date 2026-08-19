#!/usr/bin/env python
import sys, os, re, yaml, threading, subprocess, time, logging, shutil, fnmatch
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
    cnt = len(self.modified_local_files) + len(self.modified_remote_files)
    file_paths = [x[(len(self.cfg['site_dir'])+1):] for x in self.modified_local_files]
    remote_paths = [x[(len(self.backup_path)+1):] for x in self.modified_remote_files]
    site = self.cfg['site_name']
    self.logger.info(f'[backup] {site} triggered for {cnt} files, including: {file_paths[:10]} and {remote_paths[:10]}')
    self.backup_timer = None
    self.modified_local_files = []
    self.modified_remote_files = []

    for mode in self.backup_modes:
      self.logger.info(f'[backup] mode {mode}')
      if mode == 'zip':
        fp = '/usr/local/%s' % self.cfg['fn_zip']
        cmd = ['cd %s &&' % self.cfg['site_dir'], 'zip', '-r']
        cmd += ['-x "%s"' % x for x in self.backup_ignore]
        if self.quiet: cmd.append('-q')
        cmd.append(fp)
        cmd.append('.')
        sh(' '.join(cmd))
        self.s3_copy(True)
        sh('rm -rf "%s"' % fp)
      elif mode == 'sync':
        self.s3_sync(True)
      elif mode == 'copy':
        self.copy_files(file_paths, True)
      else:
        self.logger.error(f'unknown backup mode: {mode}')
      self.logger.info(f'[backup] {mode} finished {cnt}')
    self.logger.info(f'[backup] {len(self.backup_modes)} backups complete')

  def copy_files(self, files, up):
    dst_dir = self.cfg['backup_path'] if up else self.cfg['site_dir']
    src_dir = self.cfg['site_dir'] if up else self.cfg['backup_path']
    for path in files:
      dst = "%s/%s" % (dst_dir, path)
      src = "%s/%s" % (src_dir, path)
      shutil.copy(src, dst)

  # Sync the zip file up/down
  def s3_copy(self, up):
    if len(self.cfg['backup_path']) <= 0: raise Exception('backup_path required')
    fp = '/usr/local/%s' % self.cfg['fn_zip']
    s3_fp = "%s/%s" % (self.cfg['backup_path'], self.cfg['fn_zip'])
    cmd = ['aws', 's3', 'cp'] if s3_fp.startswith('s3:') else ['cp']
    if self.quiet and s3_fp.startswith('s3:'): cmd.append('--quiet')
    if up: cmd += [fp, s3_fp]
    else: cmd += [s3_fp, fp]
    return sh(' '.join(cmd))

  # Sync the local path up/down
  def s3_sync(self, up):
    if len(self.backup_path) <= 0: raise Exception('backup_path required')
    quiet = not self.logger.isEnabledFor(logging.DEBUG)
    cmd = []

    if self.backup_path.startswith('s3:'):
      cmd = ['aws', 's3', 'sync']
      cmd += ['--exclude "%s"' % x for x in self.backup_ignore]
      cmd.append('--delete')
      if self.quiet: cmd.append('--quiet')
      if up: cmd += [self.cfg['site_dir'], self.backup_path]
      else: cmd += [self.backup_path, self.cfg['site_dir']]
    else: # re-sync existing folder
      hn = os.getenv('UNISONLOCALHOSTNAME')
      cmd = ['unison', '-batch']
      if hn is not None: cmd = [f'export UNISONLOCALHOSTNAME="{hn}"', '&&'] + cmd
      if self.quiet: cmd.append('-silent')
      cmd += ['-ignore "Name %s"' % x for x in self.backup_ignore]
      cmd += [self.cfg['site_dir'], self.backup_path] # unison is bidirectional
      self.logger.info(f'[backup] SYNC with {cmd}')

    return sh(' '.join(cmd))

  # When watchdog receives a modification event, schedule a backup of the site.
  def on_any_event(self, event):
    if os.path.isdir(event.src_path): return
    if not os.path.isfile(event.src_path): return
    if event.src_path in self.modified_local_files or event.src_path in self.modified_remote_files: return
    if fnmatch.fnmatch(event.src_path, '*/.unison.*'): return
    if len(self.backup_ignore) > 0:
      if any(fnmatch.fnmatch(event.src_path, x) for x in self.backup_ignore): # and self.backup_ignore.match(event.src_path): return
        return

    # check it was REALLY modified recently
    mt = time.time() - os.path.getmtime(event.src_path)
    if mt > 60: return

    self.logger.debug('received file modification event for ' + event.src_path)

    if event.src_path.startswith(self.cfg['site_dir']): self.modified_local_files.append(event.src_path)
    elif event.src_path.startswith(self.backup_path): self.modified_remote_files.append(event.src_path)
    else: self.logger.error(f'Invalid modification path: {event.src_path}')

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
        else:
          cfg = cfg.replace("{{%s}}" % var_name.upper(), "")
      self.logger.debug(f'writing to {dst_fn}: {cfg}')
      dst.write(cfg)

  # Set up nginx & FPM for this site.
  def configure(self):
    self.logger.debug('configuring nginx, fpm, and docker')
    listen = self.cfg['listen_addr']
    if len(self.cfg['server_listen']) > 0: listen += ' ' + self.cfg['server_listen']
    nginx_cfg = OrderedDict({
      'listen': listen,
      'server_name': self.cfg['server_name'],
      'root': self.cfg['site_dir'],
      'index': 'index.php',
      'access_log': self.cfg['nginx_access_log'],
      'error_log': self.cfg['nginx_error_log'],
      'rewrite': self.cfg['rewrite'],
    })
    nginx_cfg = [f"    {k} {nginx_cfg[k]};" for k in nginx_cfg if len(nginx_cfg[k]) > 0]

    if (len(self.cfg['headers']) > 0):
      self.cfg['headers'] = f"add_header {self.cfg['headers']};";

    self.cfg['includes'] = str2list(self.cfg['includes'])

    # AUTO-include the nginx conf from the site dir
    ngx = f"{self.cfg['site_dir']}/nginx.conf"
    if os.path.isfile(ngx):
      self.cfg['includes'] += [ngx]

    self.cfg['includes'] = "\n    ".join([f"include {x};" for x in self.cfg['includes']])
    self.cfg['nginx_server_config'] = "\n".join(nginx_cfg)
    self.append_template_config("nginx", "/etc/nginx/conf.d/%s.conf" % self.cfg['site_name'])
    self.append_template_config("fpm", "/usr/local/etc/php-fpm.d/zz-%s.conf" % self.cfg['site_name'])
    # self.append_template_config("docker", "/usr/local/etc/php-fpm.d/docker.conf")
    # self.append_template_config("zz-docker", "/usr/local/etc/php-fpm.d/zz-docker.conf")

  # Download any backups for this site
  def restore(self):
    # If the copy_from directory exists first restore it that way
    if not os.path.isdir(self.copy_from):
      if os.path.isdir(self.backup_path):
        self.logger.info(f'Copying backup dir {self.backup_path} to persistant {self.cfg['copy_from']}/...')
        sh(f'cp -r {self.backup_path} {self.cfg['copy_from']}/')
        self.logger.info(f'Fixing permissions on persistant {self.cfg['copy_from']}/...')
        sh(f'chown -R www-data:www-data {self.cfg['copy_from']}')
      else:
        self.logger.error(f'No backup found at {self.backup_path}')
        return

    self.logger.info(f'Copying persistant dir {self.copy_from} to ephemeral {self.cfg['www_root']}/...')
    sh(f'cp -r {self.copy_from} {self.cfg['www_root']}/')
    self.logger.info(f'Fixing permissions on ephemeral {self.cfg['www_root']}/...')
    sh(f'chown -R www-data:www-data {self.cfg['site_dir']}')

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
    self.logger.info(f'watching local {self.cfg['site_dir']} for file changes...')
    sh(f'echo "*/15  *  *  *  *    /usr/local/bin/sync.sh {self.cfg['site_name']} local" >> /etc/crontabs/root')
    sh(f'echo "{self.cfg['sync_interval']}    /usr/local/bin/sync.sh {self.cfg['site_name']} efs" >> /etc/crontabs/root')

    # self.local_observer = Observer()
    # self.local_observer.schedule(self, path=self.cfg['site_dir'], recursive=True)
    # self.local_observer.daemon = True
    # self.local_observer.start()

    # if not self.backup_path.startswith('s3:') and 'sync' in self.backup_modes:
      # TODO: watcher doesn't work on EFS. Instead, write a sync timestamp file on sync
      # and then use a timer to watch that file for a change timestamp to trigger sync
      # self.logger.info(f'watching remote {self.backup_path} for file changes...')
      # self.remote_observer = Observer()
      # self.remote_observer.schedule(self, path=self.backup_path, recursive=True)
      # self.remote_observer.daemon = True
      # self.remote_observer.start()

  def __init__(self, site_name, fpm_port):
    # Set up the default config:
    www_root = default_cfg['www_root']
    self.cfg = {
      'fpm_port': str(fpm_port),
      'server_port': "80",
      'server_listen': "",
      'server_name': '',
      'fastcgi_params': '',
      'rewrite': '',
      'headers': '',
      'includes': '',
      'site_dir': "%s/%s" % (www_root, site_name),
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

    clean = [f"/etc/nginx/conf.d/{site_name}.conf", f"/usr/local/etc/php-fpm.d/{site_name}.conf"]
    for fp in clean:
      if os.path.isfile(fp):
        os.remove(fp)

    if len(self.cfg['server_name']) <= 0:
      server_name = f'{site_name}.com www.{site_name}.com'
      self.logger.info(f'no server_name for {site_name}; assuming it is "{server_name}"')
      self.cfg['server_name'] = server_name

    self.backup_modes = str2list(self.cfg['backup_mode'])
    self.backup_exclude = str2list(self.cfg['backup_exclude'])
    self.backup_ignore = self.backup_exclude + str2list(self.cfg['backup_ignore'])
    self.copy_from = "%s/%s" % (self.cfg['copy_from'], self.cfg['site_name'])
    self.backup_path = "%s/%s" % (self.cfg['backup_path'], self.cfg['site_name'])
    self.local_path = self.cfg['site_dir']
    self.quiet = not self.logger.isEnabledFor(logging.DEBUG)
    self.modified_local_files = []
    self.modified_remote_files = []
    self.restore()
    self.configure()
    self.install()

    if len(self.backup_modes) > 0: self.watch_for_backup()
    self.logger.info(f'succesfully configured {site_name} on port {fpm_port}')

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
def replace_placeholders(fp_in, fp_out = None):
  if not fp_out: fp_out = fp_in
  with open(fp_in, 'r') as file: cfg = file.read()
  with open(fp_out, 'w') as file:
    for var_name in default_cfg:
      ev = var_name.upper()
      cfg = cfg.replace("{{%s}}" % ev, os.getenv(ev, default_cfg[var_name]))
    logging.debug(f'writing to {fp_out}: {cfg}')
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
  'bind_addr': '[::]',
  'server_listen': "",
  'dd_agent_host': 'localhost',
  'fastcgi_params': '',
  'www_root': '/var/www/html',
  'copy_from': '/var/www/shared', # Before starting, first copy the site from this root dir if it exists
  'nginx_default_cfg': '',
  'nginx_access_log': '/dev/stdout main',
  'nginx_error_log': '/dev/stderr warn',
  # From: https://serverfault.com/questions/658367/how-to-get-php-fpm-to-log-to-stdout-stderr-when-running-in-a-docker-container
  'fpm_access_log': '/dev/null', # '/proc/self/fd/2',
  'fpm_error_log': '/proc/self/fd/2',
  'backup_path': '',
  'max_upload_size': '256M',
  'restore_policy': 'missing',
  'restore_mode': '',
  'backup_mode': '',
  'backup_exclude': "*.DS_Store *__MACOSX/* *.tmp/* *.tmb/* *temp-write-test* */cache/* *.lock *.txt *.accessed *uploads/wp-file-manager-pro* */ao_ccss/* */wc-logs/* */sitemap.xml */wp-activate.php */pinterest-*.html *.htaccess */object-cache.php",
  'backup_ignore': "", # added to backup_exclude
  'backup_delay': '30.0',
  'sync_interval': '0 2 * * *', # 2am nightly sync backup to the remote EFS server (local backup happens every 15m)
  'cache_dur': '72h', # NGINX to PHP-FPM
  # HTTP statuses to cache from PHP-FPM:
  'cache_codes': '200 301 302',
  # FPM Regex for cache-skip endpoints
  'do_not_cache_paths': '^/(cart|checkout|my-account|account-|payment-|affiliate-|wp-admin)',
  # FPM Regex for cookie names that should prevent caching
  'do_not_cache_cookies': 'wordpress_no_cache',
  # Location regex for all well known static file types
  'static_files': '(ogg|ogv|svg|svgz|mp4|rss|atom|jpg|jpeg|gif|png|ico|zip|tgz|gz|rar|bz2|doc|xls|exe|ppt|tar|mid|midi|wav|bmp|rtf|webp|css|js|eot|otf|ttf|woff|woff2)',
  # From: https://github.com/nginx/nginx/blob/master/conf/nginx.conf
  'nginx_main_log_format': ('\'$remote_addr - $remote_user [$time_local] "$request" '
                            '$status $body_bytes_sent "$http_referer" '
                            '"$http_user_agent" "$http_x_forwarded_for"\'')
}, '/etc/multipress/config.yaml')
default_cfg['listen_addr'] = default_cfg['bind_addr'] + ':' + default_cfg['server_port']
logging.basicConfig(format=default_cfg['log_format'], level=default_cfg['log_level'])

sites = {}

if __name__ == "__main__":
  replace_placeholders('/etc/nginx/nginx.conf')
  replace_placeholders('/etc/dd-config.json')
  replace_placeholders('/usr/local/etc/php-fpm.d/docker.conf')
  replace_placeholders('/usr/local/etc/php/conf.d/uploads.ini')

  sh('sed -i "s/max_execution_time = 30/max_execution_time = 300/g" /usr/local/etc/php/php.ini-production')
  sh('sed -i "s/max_execution_time = 30/max_execution_time = 300/g" /usr/local/etc/php/php.ini-development')

  fpm_port = 9000
  nginx_defaults = []
  sitenames = str2list(os.getenv('SITES', ''))
  d = '/etc/multipress/sites'
  if os.path.isdir(d):
    for item in os.listdir(d):
      if os.path.isfile(os.path.join(d, item)):
        n = os.path.splitext(item)[0]
        if not n in sitenames:
          sitenames.append(n)
  if not len(sitenames) > 0:
    raise Exception("No sites provided via the SITES enviroment variable or yaml config files")
  for site_name in sitenames:
    sites[site_name] = Site(site_name, fpm_port)
    www_root = default_cfg['www_root']

    nginx_defaults.append(f"""
    location ~ ^/{site_name}/status$ {{
        access_log off; # Disable logging
        datadog_disable; # Disable OpenTracing

        include fastcgi_params;
        fastcgi_pass localhost:{fpm_port};
        fastcgi_param SCRIPT_FILENAME {www_root}/{site_name}/status;
        fastcgi_param DD_TRACE_ENABLED "false";
        # fastcgi_param PATH_INFO $fastcgi_path_info;
    }}
    location ~ ^/{site_name}/ping$ {{
        access_log off; # Disable logging
        datadog_disable; # Disable OpenTracing

        include fastcgi_params;
        fastcgi_pass localhost:{fpm_port};
        fastcgi_param SCRIPT_FILENAME {www_root}/{site_name}/ping;
        fastcgi_param DD_TRACE_ENABLED "false";
        # fastcgi_param PATH_INFO $fastcgi_path_info;
    }}""")

    fpm_port += 1

  default_cfg['nginx_default_cfg'] = "\n".join(nginx_defaults)
  replace_placeholders('/etc/nginx-default.conf', '/etc/nginx/conf.d/default.conf')
  sh('crond')
  sh('nginx -g "daemon off;" &')
  sh('exec php-fpm')
