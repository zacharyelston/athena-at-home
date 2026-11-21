# OpenSeadragon (vendored)

The zoom viewer on item pages. Vendored so a site loads nothing from another host.

- Version: 6.1.1 (`openseadragon.min.js` from the npm package `openseadragon@6.1.1`, unmodified)
- SHA-256: `14d587cd3ee7c4861f1a41077ce642029364a21029956ba635bd8fb1202cf9ad`
- Licence: BSD-3-Clause, see `LICENSE.txt` (compatible with this project's GPL-3.0-or-later)
- Source: https://github.com/openseadragon/openseadragon

To update: `npm pack openseadragon@<version>`, copy `build/openseadragon/openseadragon.min.js` and `LICENSE.txt`
here, and update the version and checksum above and in `tests/test_site.py`. The package's button images are not
used; the viewer's buttons are the site's own.
