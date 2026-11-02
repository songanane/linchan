project(
  'linchan',
  version: '0.1.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 0.62.0',
)

app_id = 'io.github.songanane.Linchan'

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
pkgdatadir = datadir / meson.project_name()

subdir('linchan')
subdir('data')

# Launcher
conf = configuration_data()
conf.set('pkgdatadir', pkgdatadir)
launcher = configure_file(
  input: 'bin/linchan.in',
  output: 'linchan-launcher',
  configuration: conf,
)
install_data(
  launcher,
  rename: 'linchan',
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)
