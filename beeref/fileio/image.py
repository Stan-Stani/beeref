# This file is part of BeeRef.
#
# BeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BeeRef.  If not, see <https://www.gnu.org/licenses/>.

import logging
import os.path
import tempfile
from urllib.error import URLError
from urllib import parse, request

from PyQt6 import QtGui

import exif
from lxml import etree
import plum


logger = logging.getLogger(__name__)


def exif_rotated_image(path=None):
    """Returns a QImage that is transformed according to the source's
    orientation EXIF data.
    """

    img = QtGui.QImage(path)
    if img.isNull():
        return img

    with open(path, 'rb') as f:
        try:
            exifimg = exif.Image(f)
        except (plum.exceptions.UnpackError, NotImplementedError):
            logger.exception(f'Exif parser failed on image: {path}')
            return img

    try:
        if 'orientation' in exifimg.list_all():
            orientation = exifimg.orientation
        else:
            return img
    except (NotImplementedError, ValueError):
        logger.exception(f'Exif failed reading orientation of image: {path}')
        return img

    transform = QtGui.QTransform()

    if orientation == exif.Orientation.TOP_RIGHT:
        return img.mirrored(horizontal=True, vertical=False)
    if orientation == exif.Orientation.BOTTOM_RIGHT:
        transform.rotate(180)
        return img.transformed(transform)
    if orientation == exif.Orientation.BOTTOM_LEFT:
        return img.mirrored(horizontal=False, vertical=True)
    if orientation == exif.Orientation.LEFT_TOP:
        transform.rotate(90)
        return img.transformed(transform).mirrored(
            horizontal=True, vertical=False)
    if orientation == exif.Orientation.RIGHT_TOP:
        transform.rotate(90)
        return img.transformed(transform)
    if orientation == exif.Orientation.RIGHT_BOTTOM:
        transform.rotate(270)
        return img.transformed(transform).mirrored(
            horizontal=True, vertical=False)
    if orientation == exif.Orientation.LEFT_BOTTOM:
        transform.rotate(270)
        return img.transformed(transform)

    return img


# Some image hosts (e.g. unsplash) reject requests without a browser-like
# User-Agent, so identify ourselves as a real client while still naming
# BeeRef.
USER_AGENT = ('Mozilla/5.0 (compatible; BeeRef reference image viewer; '
              '+https://beeref.org)')


def _urlopen(url):
    return request.urlopen(
        request.Request(url, headers={'User-Agent': USER_AGENT}))


def _image_from_bytes(data):
    """Build an exif-rotated QImage from raw image bytes, or a null QImage
    if the data isn't a loadable image."""
    with tempfile.TemporaryDirectory() as tmp:
        fname = os.path.join(tmp, 'img')
        with open(fname, 'wb') as f:
            f.write(data)
            logger.debug(f'Temporarily saved in: {fname}')
        return exif_rotated_image(fname)


def image_url_from_html(data, base_url):
    """Find the most likely image URL referenced by an HTML page.

    Prefers the page's declared social-preview image (og:image /
    twitter:image), which is how most image hosts (unsplash, pinterest,
    flickr, ...) expose the canonical full-size image, and falls back to
    the first ``<img>`` on the page. Relative URLs are resolved against
    ``base_url``. Returns ``None`` if nothing suitable is found."""
    try:
        root = etree.HTML(data)
    except Exception as e:
        logger.debug(f'Could not parse page for an image url: {e}')
        return None
    if root is None:
        return None
    candidates = (
        root.xpath('//meta[@property="og:image"]/@content')
        + root.xpath('//meta[@name="og:image"]/@content')
        + root.xpath('//meta[@name="twitter:image"]/@content')
        + root.xpath('//meta[@name="twitter:image:src"]/@content')
        + root.xpath('//link[@rel="image_src"]/@href')
        + root.xpath('//img/@src'))
    for candidate in candidates:
        if candidate:
            return parse.urljoin(base_url, candidate)
    return None


def download_image(url):
    """Download an image from a remote URL.

    If the URL doesn't point at a direct image but at an HTML page (for
    example when an image is dragged from a website, which often hands over
    the page link rather than the image itself), the page is inspected for
    the image it references and that is downloaded instead. Returns a tuple
    of the loaded QImage (null on failure) and the URL it was ultimately
    loaded from."""
    try:
        data = _urlopen(url).read()
    except URLError as e:
        logger.debug(f'Downloading image failed: {e.reason}')
        return (QtGui.QImage(), url)

    img = _image_from_bytes(data)
    if not img.isNull():
        return (img, url)

    # Not a direct image; treat the response as a web page and look for the
    # actual image it references.
    image_url = image_url_from_html(data, url)
    if not image_url or image_url == url:
        logger.debug(f'No image found on page: {url}')
        return (QtGui.QImage(), url)
    logger.debug(f'Following image url from page: {image_url}')
    try:
        data = _urlopen(image_url).read()
    except URLError as e:
        logger.debug(f'Downloading image failed: {e.reason}')
        return (QtGui.QImage(), image_url)
    return (_image_from_bytes(data), image_url)


def source_for(path):
    """A human-readable record of where an image is being loaded from, used
    to remember an image's origin. ``path`` is the original file path or URL
    (as passed to ``load_image``)."""
    if isinstance(path, str):
        return path
    if path.isLocalFile():
        return path.toLocalFile()
    return path.toString()


def load_image(path):
    if isinstance(path, str):
        path = os.path.normpath(path)
        return (exif_rotated_image(path), path)
    if path.isLocalFile():
        path = os.path.normpath(path.toLocalFile())
        return (exif_rotated_image(path), path)

    return download_image(bytes(path.toEncoded()).decode())
