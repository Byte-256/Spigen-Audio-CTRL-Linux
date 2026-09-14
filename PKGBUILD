pkgname=spigen-audio-ctrl
pkgver=2.0.0
pkgrel=1
pkgdesc='Native Linux controller for Spigen SA-HP P10 headphones'
arch=('any')
url='https://github.com/Depth118/Spigen-Audio-CTRL-Linux'
license=('MIT')
depends=('bluez' 'gtk4' 'python' 'python-cairo' 'python-dbus' 'python-gobject')
makedepends=('git')
source=("git+${url}.git#tag=v${pkgver}")
b2sums=('SKIP')

package() {
  cd "$srcdir/Spigen-Audio-CTRL-Linux"
  make DESTDIR="$pkgdir" PREFIX=/usr install
}
