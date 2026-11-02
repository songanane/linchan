desktop_file = 'io.github.songanane.Linchan.desktop'
metainfo_file = 'io.github.songanane.Linchan.metainfo.xml'

install_data(desktop_file, install_dir: datadir / 'applications')
install_data(metainfo_file, install_dir: datadir / 'metainfo')

install_data(
  'icons/hicolor/scalable/apps/@0@.svg'.format(app_id),
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'icons/hicolor/symbolic/apps/@0@-symbolic.svg'.format(app_id),
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

desktop_validate = find_program('desktop-file-validate', required: false)
if desktop_validate.found()
  test('Validate desktop file', desktop_validate, args: [files(desktop_file)])
endif

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('Validate metainfo', appstreamcli,
       args: ['validate', '--no-net', '--explain', files(metainfo_file)])
endif
