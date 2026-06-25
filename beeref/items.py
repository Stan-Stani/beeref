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

"""Classes for items that are added to the scene by the user (images,
text).
"""

from collections import defaultdict
from functools import cached_property
import logging
import os.path

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from beeref import commands
from beeref.config import BeeSettings
from beeref.constants import COLORS
from beeref.selection import SelectableMixin


logger = logging.getLogger(__name__)

item_registry = {}


def register_item(cls):
    item_registry[cls.TYPE] = cls
    return cls


def sort_by_filename(items):
    """Order items by filename.

    Items with a filename (ordered by filename) first, then items
    without a filename but with a save_id follow (ordered by
    save_id), then remaining items in the order that they have
    been inserted into the scene.
    """

    items_by_filename = []
    items_by_save_id = []
    items_remaining = []

    for item in items:
        if getattr(item, 'filename', None):
            items_by_filename.append(item)
        elif getattr(item, 'save_id', None):
            items_by_save_id.append(item)
        else:
            items_remaining.append(item)

    items_by_filename.sort(key=lambda x: x.filename)
    items_by_save_id.sort(key=lambda x: x.save_id)
    return items_by_filename + items_by_save_id + items_remaining


class BeeItemMixin(SelectableMixin):
    """Base for all items added by the user."""

    def set_pos_center(self, pos):
        """Sets the position using the item's center as the origin point."""

        self.setPos(pos - self.center_scene_coords)

    def has_selection_outline(self):
        return self.isSelected()

    def has_selection_handles(self):
        return (self.isSelected()
                and self.scene()
                and self.scene().has_single_selection())

    def selection_action_items(self):
        """The items affected by selection actions like scaling and rotating.
        """
        return [self]

    def on_selected_change(self, value):
        if (value and self.scene()
                and not self.scene().has_selection()
                and not self.scene().active_mode is None):
            self.bring_to_front()

    def update_from_data(self, **kwargs):
        self.save_id = kwargs.get('save_id', self.save_id)
        self.setPos(kwargs.get('x', self.pos().x()),
                    kwargs.get('y', self.pos().y()))
        self.setZValue(kwargs.get('z', self.zValue()))
        self.setScale(kwargs.get('scale', self.scale()))
        self.setRotation(kwargs.get('rotation', self.rotation()))
        if kwargs.get('flip', 1) != self.flip():
            self.do_flip()


@register_item
class BeePixmapItem(BeeItemMixin, QtWidgets.QGraphicsPixmapItem):
    """Class for images added by the user."""

    TYPE = 'pixmap'
    CROP_HANDLE_SIZE = 15

    # Line art overlay defaults: turn a sketch into tinted line art on a
    # transparent background so it can be overlaid on a reference image.
    LINEART_DEFAULT_THRESHOLD = 128
    LINEART_DEFAULT_COLOR = (255, 0, 255)
    # Width of the luminance ramp used to anti-alias the line edges.
    LINEART_SOFTNESS = 8

    def __init__(self, image, filename=None, **kwargs):
        super().__init__(QtGui.QPixmap.fromImage(image))
        self.save_id = None
        self.filename = filename
        self.reset_crop()
        logger.debug(f'Initialized {self}')
        self.is_image = True
        self.crop_mode = False
        self.init_selectable()
        self.settings = BeeSettings()
        self._lineart = False
        self._lineart_threshold = self.LINEART_DEFAULT_THRESHOLD
        self._lineart_color = QtGui.QColor(*self.LINEART_DEFAULT_COLOR)
        self._lineart_pixmap = None
        self._contrast = 1
        self._contrast_pixmap = None
        self.grayscale = False

    @classmethod
    def create_from_data(self, **kwargs):
        item = kwargs.pop('item')
        data = kwargs.pop('data', {})
        item.filename = item.filename or data.get('filename')
        if 'crop' in data:
            item.crop = QtCore.QRectF(*data['crop'])
        item.setOpacity(data.get('opacity', 1))
        item.grayscale = data.get('grayscale', False)
        item.contrast = data.get('contrast', 1)
        item.set_lineart(
            enabled=data.get('lineart', False),
            threshold=data.get(
                'lineart_threshold', self.LINEART_DEFAULT_THRESHOLD),
            color=QtGui.QColor(*data.get(
                'lineart_color', self.LINEART_DEFAULT_COLOR)))
        return item

    def __str__(self):
        size = self.pixmap().size()
        return (f'Image "{self.filename}" {size.width()} x {size.height()}')

    @property
    def crop(self):
        return self._crop

    @crop.setter
    def crop(self, value):
        logger.debug(f'Setting crop for {self} to {value}')
        self.prepareGeometryChange()
        self._crop = value
        self.update()

    @property
    def grayscale(self):
        return self._grayscale

    @grayscale.setter
    def grayscale(self, value):
        logger.debug('Setting grayscale for {self} to {value}')
        self._grayscale = value
        if value is True:
            # Using the grayscale image format to convert to grayscale
            # loses an image's tranparency. So the straightworward
            # following method gives us an ugly black replacement:
            # img = img.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)

            # Instead, we will fill the background with the current
            # canvas colour, so the issue is only visible if the image
            # overlaps other images. The way we do it here only works
            # as long as the canvas colour is itself grayscale,
            # though.
            img = QtGui.QImage(
                self.pixmap().size(), QtGui.QImage.Format.Format_Grayscale8)
            img.fill(QtGui.QColor(*COLORS['Scene:Canvas']))
            painter = QtGui.QPainter(img)
            painter.drawPixmap(0, 0, self.pixmap())
            painter.end()
            self._grayscale_pixmap = QtGui.QPixmap.fromImage(img)

            # Alternative methods that have their own issues:
            #
            # 1. Use setAlphaChannel of the resulting grayscale
            # image. How do we get the original alpha channel? Using
            # the whole original image also takes color values into
            # account, not just their alpha values.
            #
            # 2. QtWidgets.QGraphicsColorizeEffect() with black colour
            # on the GraphicsItem. This applys to everything the paint
            # method does, so the selection outline/handles will also
            # be gray. setGraphicsEffect is only available on some
            # widgets, so we can't apply it selectively.
            #
            # 3. Going through every pixel and doing it manually — bad
            # performance.
        else:
            self._grayscale_pixmap = None

        self._update_contrast_pixmap()

    @property
    def contrast(self):
        return self._contrast

    @contrast.setter
    def contrast(self, value):
        logger.debug(f'Setting contrast for {self} to {value}')
        self._contrast = value
        self._update_contrast_pixmap()

    @staticmethod
    def apply_contrast(img, factor):
        """Return a copy of ``img`` with the given contrast ``factor``
        applied (1 = unchanged, 0 = flat gray, >1 = more contrast).

        The alpha channel is left untouched.
        """
        # Linear contrast around the mid grey point, as a 256-entry
        # lookup table: new = (old - 128) * factor + 128
        lut = bytes(max(0, min(255, round((i - 128) * factor + 128)))
                    for i in range(256))
        img = img.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
        ptr = img.bits()
        ptr.setsize(img.sizeInBytes())
        src = bytes(ptr)
        out = bytearray(src.translate(lut))
        # Restore the original alpha values (every 4th byte)
        out[3::4] = src[3::4]
        result = QtGui.QImage(
            bytes(out), img.width(), img.height(),
            img.bytesPerLine(), QtGui.QImage.Format.Format_RGBA8888)
        # Detach from the local buffer before it goes out of scope
        return result.copy()

    def _update_contrast_pixmap(self):
        """Recompute the cached pixmap shown on the canvas, applying
        contrast on top of the (possibly grayscale) base pixmap."""
        base = self._grayscale_pixmap if self.grayscale else self.pixmap()
        if self._contrast == 1 or base is None or base.isNull():
            self._contrast_pixmap = None
        else:
            self._contrast_pixmap = QtGui.QPixmap.fromImage(
                self.apply_contrast(base.toImage(), self._contrast))
        # Line art sits on top of contrast, so it has to follow along.
        self._update_lineart_pixmap()

    @property
    def lineart(self):
        return self._lineart

    @lineart.setter
    def lineart(self, value):
        self.set_lineart(enabled=value)

    @property
    def lineart_threshold(self):
        return self._lineart_threshold

    @lineart_threshold.setter
    def lineart_threshold(self, value):
        self.set_lineart(threshold=value)

    @property
    def lineart_color(self):
        return self._lineart_color

    @lineart_color.setter
    def lineart_color(self, value):
        self.set_lineart(color=value)

    def set_lineart(self, enabled=None, threshold=None, color=None):
        """Update one or more line art settings and recompute once."""
        logger.debug(
            f'Setting lineart for {self} to enabled={enabled} '
            f'threshold={threshold} color={color}')
        if enabled is not None:
            self._lineart = enabled
        if threshold is not None:
            self._lineart_threshold = threshold
        if color is not None:
            self._lineart_color = QtGui.QColor(color)
        self._update_lineart_pixmap()

    @classmethod
    def apply_lineart(cls, img, threshold, color):
        """Return a tinted line art version of ``img`` on a transparent
        background: pixels darker than ``threshold`` become ``color``,
        lighter pixels become transparent. The source's own transparency
        is preserved.
        """
        softness = cls.LINEART_SOFTNESS

        # Map luminance to alpha: pixels at or below the threshold are
        # fully opaque lines, with a short ramp just above the threshold
        # to smooth (anti-alias) the line edges.
        def alpha_for(lum):
            if lum <= threshold:
                return 255
            if lum >= threshold + softness:
                return 0
            return round(255 * (threshold + softness - lum) / softness)
        alpha_lut = bytes(alpha_for(i) for i in range(256))

        # Luminance via Qt's grayscale conversion (done in C), then the
        # lookup table turns it into an alpha mask.
        gray = img.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        ptr = gray.bits()
        ptr.setsize(gray.sizeInBytes())
        alpha_bytes = bytes(ptr).translate(alpha_lut)
        alpha_img = QtGui.QImage(
            alpha_bytes, gray.width(), gray.height(),
            gray.bytesPerLine(),
            QtGui.QImage.Format.Format_Grayscale8).copy()

        # Solid tint masked by the luminance-derived alpha.
        result = QtGui.QImage(
            img.size(), QtGui.QImage.Format.Format_RGBA8888)
        result.fill(QtGui.QColor(color.red(), color.green(), color.blue()))
        result.setAlphaChannel(alpha_img)

        # Keep lines only where the source was opaque to begin with.
        if img.hasAlphaChannel():
            painter = QtGui.QPainter(result)
            painter.setCompositionMode(
                QtGui.QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, img)
            painter.end()
        return result

    @classmethod
    def auto_threshold(cls, img):
        """Pick a luminance threshold separating dark lines from the
        lighter background, using Otsu's method on the histogram."""
        if img.isNull():
            return cls.LINEART_DEFAULT_THRESHOLD
        gray = img.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        # The threshold is a global statistic, so a downscaled image gives
        # the same answer far more cheaply for large images.
        limit = 256
        if gray.width() > limit or gray.height() > limit:
            gray = gray.scaled(
                limit, limit, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        width, height = gray.width(), gray.height()
        bytes_per_line = gray.bytesPerLine()
        ptr = gray.bits()
        ptr.setsize(gray.sizeInBytes())
        data = bytes(ptr)

        hist = [0] * 256
        for y in range(height):
            # Skip any row padding past the actual pixels.
            for value in data[y * bytes_per_line:y * bytes_per_line + width]:
                hist[value] += 1

        total = width * height
        if total == 0:
            return cls.LINEART_DEFAULT_THRESHOLD

        sum_all = sum(i * hist[i] for i in range(256))
        sum_below = 0
        weight_below = 0
        max_variance = -1.0
        threshold = cls.LINEART_DEFAULT_THRESHOLD
        for level in range(256):
            weight_below += hist[level]
            if weight_below == 0:
                continue
            weight_above = total - weight_below
            if weight_above == 0:
                break
            sum_below += level * hist[level]
            mean_below = sum_below / weight_below
            mean_above = (sum_all - sum_below) / weight_above
            variance = (weight_below * weight_above
                        * (mean_below - mean_above) ** 2)
            if variance > max_variance:
                max_variance = variance
                threshold = level
        return threshold

    def lineart_auto_threshold(self):
        """Auto threshold for this item's current (grayscale/contrast)
        base pixmap."""
        base = self._lineart_base()
        if base is None or base.isNull():
            return self.LINEART_DEFAULT_THRESHOLD
        return self.auto_threshold(base.toImage())

    def _lineart_base(self):
        """The pixmap line art is computed from: the contrast/grayscale
        result if present, otherwise the original pixmap."""
        if self._contrast_pixmap is not None:
            return self._contrast_pixmap
        if self.grayscale:
            return self._grayscale_pixmap
        return self.pixmap()

    def _update_lineart_pixmap(self):
        """Recompute the cached line art pixmap on top of the (possibly
        grayscale and contrast-adjusted) base pixmap."""
        base = self._lineart_base()
        if not self._lineart or base is None or base.isNull():
            self._lineart_pixmap = None
        else:
            self._lineart_pixmap = QtGui.QPixmap.fromImage(
                self.apply_lineart(base.toImage(), self._lineart_threshold,
                                   self._lineart_color))
        self.update()

    def display_pixmap(self):
        """The pixmap shown on the canvas, with grayscale, contrast and
        line art applied."""
        if self._lineart_pixmap is not None:
            return self._lineart_pixmap
        if self._contrast_pixmap is not None:
            return self._contrast_pixmap
        if self.grayscale:
            return self._grayscale_pixmap
        return self.pixmap()

    def sample_color_at(self, pos):
        ipos = self.mapFromScene(pos)
        img = self.display_pixmap().toImage()

        color = img.pixelColor(int(ipos.x()), int(ipos.y()))
        if color.alpha():
            return color

    def bounding_rect_unselected(self):
        if self.crop_mode:
            return QtWidgets.QGraphicsPixmapItem.boundingRect(self)
        else:
            return self.crop

    def get_extra_save_data(self):
        return {'filename': self.filename,
                'opacity': self.opacity(),
                'grayscale': self.grayscale,
                'contrast': self.contrast,
                'lineart': self.lineart,
                'lineart_threshold': self.lineart_threshold,
                'lineart_color': [self.lineart_color.red(),
                                  self.lineart_color.green(),
                                  self.lineart_color.blue()],
                'crop': [self.crop.topLeft().x(),
                         self.crop.topLeft().y(),
                         self.crop.width(),
                         self.crop.height()]}

    def get_filename_for_export(self, imgformat, save_id_default=None):
        save_id = self.save_id or save_id_default
        assert save_id is not None

        if self.filename:
            basename = os.path.splitext(os.path.basename(self.filename))[0]
            return f'{save_id:04}-{basename}.{imgformat}'
        else:
            return f'{save_id:04}.{imgformat}'

    def get_imgformat(self, img):
        """Determines the format for storing this image."""

        formt = self.settings.valueOrDefault('Items/image_storage_format')

        if formt == 'best':
            # Images with alpha channel and small images are stored as png
            if (img.hasAlphaChannel()
                    or (img.height() < 500 and img.width() < 500)):
                formt = 'png'
            else:
                formt = 'jpg'

        logger.debug(f'Found format {formt} for {self}')
        return formt

    def pixmap_to_bytes(self, apply_grayscale=False, apply_contrast=False,
                        apply_lineart=False, apply_crop=False):
        """Convert the pixmap data to PNG bytestring."""
        barray = QtCore.QByteArray()
        buffer = QtCore.QBuffer(barray)
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if apply_grayscale and self.grayscale:
            pm = self._grayscale_pixmap
        else:
            pm = self.pixmap()

        if apply_contrast and self.contrast != 1:
            pm = QtGui.QPixmap.fromImage(
                self.apply_contrast(pm.toImage(), self.contrast))

        if apply_lineart and self.lineart:
            pm = QtGui.QPixmap.fromImage(
                self.apply_lineart(pm.toImage(), self.lineart_threshold,
                                   self.lineart_color))

        if apply_crop:
            pm = pm.copy(self.crop.toRect())

        img = pm.toImage()
        imgformat = self.get_imgformat(img)
        img.save(buffer, imgformat.upper(), quality=90)
        return (barray.data(), imgformat)

    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        self.reset_crop()

    def pixmap_from_bytes(self, data):
        """Set image pimap from a bytestring."""
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(data)
        self.setPixmap(pixmap)

    def create_copy(self):
        item = BeePixmapItem(QtGui.QImage(), self.filename)
        item.setPixmap(self.pixmap())
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        item.setOpacity(self.opacity())
        item.grayscale = self.grayscale
        item.contrast = self.contrast
        item.set_lineart(
            enabled=self.lineart,
            threshold=self.lineart_threshold,
            color=self.lineart_color)
        if self.flip() == -1:
            item.do_flip()
        item.crop = self.crop
        return item

    @cached_property
    def color_gamut(self):
        logger.debug(f'Calculating color gamut for {self}')
        gamut = defaultdict(int)
        img = self.pixmap().toImage()
        # Don't evaluate every pixel for larger images:
        step = max(1, int(max(img.width(), img.height()) / 1000))
        logger.debug(f'Considering every {step}. row/column')

        # Not actually faster than solution below :(
        # ptr = img.bits()
        # size = img.sizeInBytes()
        # pixelsize = int(img.sizeInBytes() / img.width() / img.height())
        # ptr.setsize(size)
        # for pixel in batched(ptr, n=pixelsize):
        #     r, g, b, alpha = tuple(map(ord, pixel))
        #     if 5 < alpha and 5 < r < 250 and 5 < g < 250 and 5 < b < 250:
        #         # Only consider pixels that aren't close to
        #         # transparent, white or black
        #         rgb = QtGui.QColor(r, g, b)
        #         gamut[rgb.hue(), rgb.saturation()] += 1

        for i in range(0, img.width(), step):
            for j in range(0, img.height(), step):
                rgb = img.pixelColor(i, j)
                rgbtuple = (rgb.red(), rgb.blue(), rgb.green())
                if (5 < rgb.alpha()
                        and min(rgbtuple) < 250 and max(rgbtuple) > 5):
                    # Only consider pixels that aren't close to
                    # transparent, white or black
                    gamut[rgb.hue(), rgb.saturation()] += 1

        logger.debug(f'Got {len(gamut)} color gamut values')
        return gamut

    def copy_to_clipboard(self, clipboard):
        clipboard.setPixmap(self.pixmap())

    def reset_crop(self):
        self.crop = QtCore.QRectF(
            0, 0, self.pixmap().size().width(), self.pixmap().size().height())

    @property
    def crop_handle_size(self):
        return self.fixed_length_for_viewport(self.CROP_HANDLE_SIZE)

    def crop_handle_topleft(self):
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x(),
            topleft.y(),
            self.crop_handle_size,
            self.crop_handle_size)

    def crop_handle_bottomleft(self):
        bottomleft = self.crop_temp.bottomLeft()
        return QtCore.QRectF(
            bottomleft.x(),
            bottomleft.y() - self.crop_handle_size,
            self.crop_handle_size,
            self.crop_handle_size)

    def crop_handle_bottomright(self):
        bottomright = self.crop_temp.bottomRight()
        return QtCore.QRectF(
            bottomright.x() - self.crop_handle_size,
            bottomright.y() - self.crop_handle_size,
            self.crop_handle_size,
            self.crop_handle_size)

    def crop_handle_topright(self):
        topright = self.crop_temp.topRight()
        return QtCore.QRectF(
            topright.x() - self.crop_handle_size,
            topright.y(),
            self.crop_handle_size,
            self.crop_handle_size)

    def crop_handles(self):
        return (self.crop_handle_topleft,
                self.crop_handle_bottomleft,
                self.crop_handle_bottomright,
                self.crop_handle_topright)

    def crop_edge_top(self):
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x() + self.crop_handle_size,
            topleft.y(),
            self.crop_temp.width() - 2 * self.crop_handle_size,
            self.crop_handle_size)

    def crop_edge_left(self):
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x(),
            topleft.y() + self.crop_handle_size,
            self.crop_handle_size,
            self.crop_temp.height() - 2 * self.crop_handle_size)

    def crop_edge_bottom(self):
        bottomleft = self.crop_temp.bottomLeft()
        return QtCore.QRectF(
            bottomleft.x() + self.crop_handle_size,
            bottomleft.y() - self.crop_handle_size,
            self.crop_temp.width() - 2 * self.crop_handle_size,
            self.crop_handle_size)

    def crop_edge_right(self):
        topright = self.crop_temp.topRight()
        return QtCore.QRectF(
            topright.x() - self.crop_handle_size,
            topright.y() + self.crop_handle_size,
            self.crop_handle_size,
            self.crop_temp.height() - 2 * self.crop_handle_size)

    def crop_edges(self):
        return (self.crop_edge_top,
                self.crop_edge_left,
                self.crop_edge_bottom,
                self.crop_edge_right)

    def get_crop_handle_cursor(self, handle):
        """Gets the crop cursor for the given handle."""

        is_topleft_or_bottomright = handle in (
            self.crop_handle_topleft, self.crop_handle_bottomright)
        return self.get_diag_cursor(is_topleft_or_bottomright)

    def get_crop_edge_cursor(self, edge):
        """Gets the crop edge cursor for the given edge."""

        top_or_bottom = edge in (
            self.crop_edge_top, self.crop_edge_bottom)
        sideways = (45 < self.rotation() < 135
                    or 225 < self.rotation() < 315)

        if top_or_bottom is sideways:
            return Qt.CursorShape.SizeHorCursor
        else:
            return Qt.CursorShape.SizeVerCursor

    def draw_crop_rect(self, painter, rect):
        """Paint a dotted rectangle for the cropping UI."""
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255))
        pen.setWidth(2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect)
        pen.setColor(QtGui.QColor(0, 0, 0))
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.drawRect(rect)

    def paint(self, painter, option, widget):
        if abs(painter.combinedTransform().m11()) < 2:
            # We want image smoothing, but only for images where we
            # are not zoomed in a lot. This is to ensure that for
            # example icons and pixel sprites can be viewed correctly.
            painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)

        if self.crop_mode:
            self.paint_debug(painter, option, widget)

            # Darken image outside of cropped area
            painter.drawPixmap(0, 0, self.pixmap())
            path = QtWidgets.QGraphicsPixmapItem.shape(self)
            path.addRect(self.crop_temp)
            color = QtGui.QColor(0, 0, 0)
            color.setAlpha(100)
            painter.setBrush(QtGui.QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
            painter.setBrush(QtGui.QBrush())

            for handle in self.crop_handles():
                self.draw_crop_rect(painter, handle())
            self.draw_crop_rect(painter, self.crop_temp)
        else:
            painter.drawPixmap(self.crop, self.display_pixmap(), self.crop)
            self.paint_selectable(painter, option, widget)

    def enter_crop_mode(self):
        logger.debug(f'Entering crop mode on {self}')
        self.prepareGeometryChange()
        self.crop_mode = True
        self.crop_temp = QtCore.QRectF(self.crop)
        self.crop_mode_move = None
        self.crop_mode_event_start = None
        self.grabKeyboard()
        self.update()
        self.scene().crop_item = self

    def exit_crop_mode(self, confirm):
        logger.debug(f'Exiting crop mode with {confirm} on {self}')
        if confirm and self.crop != self.crop_temp:
            self.scene().undo_stack.push(
                commands.CropItem(self, self.crop_temp))
        self.prepareGeometryChange()
        self.crop_mode = False
        self.crop_temp = None
        self.crop_mode_move = None
        self.crop_mode_event_start = None
        self.ungrabKeyboard()
        self.update()
        self.scene().crop_item = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.exit_crop_mode(confirm=True)
        elif event.key() == Qt.Key.Key_Escape:
            self.exit_crop_mode(confirm=False)
        else:
            super().keyPressEvent(event)

    def hoverMoveEvent(self, event):
        if not self.crop_mode:
            return super().hoverMoveEvent(event)

        for handle in self.crop_handles():
            if handle().contains(event.pos()):
                self.set_cursor(self.get_crop_handle_cursor(handle))
                return
        for edge in self.crop_edges():
            if edge().contains(event.pos()):
                self.set_cursor(self.get_crop_edge_cursor(edge))
                return
        self.unset_cursor()

    def mousePressEvent(self, event):
        if not self.crop_mode:
            return super().mousePressEvent(event)

        event.accept()
        for handle in self.crop_handles():
            # Click into a handle?
            if handle().contains(event.pos()):
                self.crop_mode_event_start = event.pos()
                self.crop_mode_move = handle
                return
        for edge in self.crop_edges():
            # Click into an edge handle?
            if edge().contains(event.pos()):
                self.crop_mode_event_start = event.pos()
                self.crop_mode_move = edge
                return
        # Click not in handle, end cropping mode:
        self.exit_crop_mode(
            confirm=self.crop_temp.contains(event.pos()))

    def ensure_point_within_crop_bounds(self, point, handle):
        """Returns the point, or the nearest point within the pixmap."""

        if handle == self.crop_handle_topleft:
            topleft = QtCore.QPointF(0, 0)
            bottomright = self.crop_temp.bottomRight()
        if handle == self.crop_handle_bottomleft:
            topleft = QtCore.QPointF(0, self.crop_temp.top())
            bottomright = QtCore.QPointF(
                self.crop_temp.right(), self.pixmap().size().height())
        if handle == self.crop_handle_bottomright:
            topleft = self.crop_temp.topLeft()
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height())
        if handle == self.crop_handle_topright:
            topleft = QtCore.QPointF(self.crop_temp.left(), 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.crop_temp.bottom())
        if handle == self.crop_edge_top:
            topleft = QtCore.QPointF(0, 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.crop_temp.bottom())
        if handle == self.crop_edge_bottom:
            topleft = QtCore.QPointF(0, self.crop_temp.top())
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height())
        if handle == self.crop_edge_left:
            topleft = QtCore.QPointF(0, 0)
            bottomright = QtCore.QPointF(
                self.crop_temp.right(), self.pixmap().size().height())
        if handle == self.crop_edge_right:
            topleft = QtCore.QPointF(self.crop_temp.left(), 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height())

        point.setX(min(bottomright.x(), max(topleft.x(), point.x())))
        point.setY(min(bottomright.y(), max(topleft.y(), point.y())))

        return point

    def mouseMoveEvent(self, event):
        if self.crop_mode and self.crop_mode_event_start:
            diff = event.pos() - self.crop_mode_event_start
            if self.crop_mode_move == self.crop_handle_topleft:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move)
                self.crop_temp.setTopLeft(new)
            if self.crop_mode_move == self.crop_handle_bottomleft:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomLeft() + diff, self.crop_mode_move)
                self.crop_temp.setBottomLeft(new)
            if self.crop_mode_move == self.crop_handle_bottomright:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomRight() + diff, self.crop_mode_move)
                self.crop_temp.setBottomRight(new)
            if self.crop_mode_move == self.crop_handle_topright:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topRight() + diff, self.crop_mode_move)
                self.crop_temp.setTopRight(new)
            if self.crop_mode_move == self.crop_edge_top:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move)
                self.crop_temp.setTop(new.y())
            if self.crop_mode_move == self.crop_edge_left:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move)
                self.crop_temp.setLeft(new.x())
            if self.crop_mode_move == self.crop_edge_bottom:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomLeft() + diff, self.crop_mode_move)
                self.crop_temp.setBottom(new.y())
            if self.crop_mode_move == self.crop_edge_right:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topRight() + diff, self.crop_mode_move)
                self.crop_temp.setRight(new.x())
            self.update()
            self.crop_mode_event_start = event.pos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.crop_mode:
            self.crop_mode_move = None
            self.crop_mode_event_start = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


@register_item
class BeeTextItem(BeeItemMixin, QtWidgets.QGraphicsTextItem):
    """Class for text added by the user."""

    TYPE = 'text'

    def __init__(self, text=None, **kwargs):
        super().__init__(text or "Text")
        self.save_id = None
        logger.debug(f'Initialized {self}')
        self.is_image = False
        self.init_selectable()
        self.is_editable = True
        self.edit_mode = False
        self.setDefaultTextColor(QtGui.QColor(*COLORS['Scene:Text']))

    @classmethod
    def create_from_data(cls, **kwargs):
        data = kwargs.get('data', {})
        item = cls(**data)
        return item

    def __str__(self):
        txt = self.toPlainText()[:40]
        return (f'Text "{txt}"')

    def get_extra_save_data(self):
        return {'text': self.toPlainText()}

    def contains(self, point):
        return self.boundingRect().contains(point)

    def paint(self, painter, option, widget):
        painter.setPen(Qt.PenStyle.NoPen)
        color = QtGui.QColor(0, 0, 0)
        color.setAlpha(40)
        brush = QtGui.QBrush(color)
        painter.setBrush(brush)
        painter.drawRect(QtWidgets.QGraphicsTextItem.boundingRect(self))
        option.state = QtWidgets.QStyle.StateFlag.State_Enabled
        super().paint(painter, option, widget)
        self.paint_selectable(painter, option, widget)

    def create_copy(self):
        item = BeeTextItem(self.toPlainText())
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        if self.flip() == -1:
            item.do_flip()
        return item

    def enter_edit_mode(self):
        logger.debug(f'Entering edit mode on {self}')
        self.edit_mode = True
        self.old_text = self.toPlainText()
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction)
        self.scene().edit_item = self

    def exit_edit_mode(self, commit=True):
        logger.debug(f'Exiting edit mode on {self}')
        self.edit_mode = False
        # reset selection:
        self.setTextCursor(QtGui.QTextCursor(self.document()))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.scene().edit_item = None
        if commit:
            self.scene().undo_stack.push(
                commands.ChangeText(self, self.toPlainText(), self.old_text))
            if not self.toPlainText().strip():
                logger.debug('Removing empty text item')
                self.scene().undo_stack.push(
                    commands.DeleteItems(self.scene(), [self]))
        else:
            self.setPlainText(self.old_text)

    def has_selection_handles(self):
        return super().has_selection_handles() and not self.edit_mode

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.exit_edit_mode()
            event.accept()
            return
        if (event.key() == Qt.Key.Key_Escape
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.exit_edit_mode(commit=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_to_clipboard(self, clipboard):
        clipboard.setText(self.toPlainText())


@register_item
class BeeErrorItem(BeeItemMixin, QtWidgets.QGraphicsTextItem):
    """Class for displaying error messages when an item can't be loaded
    from a bee file.

    This item will be displayed instead of the original item. It won't
    save to bee files. The original item will be preserved in the bee
    file, unless this item gets deleted by the user, or a new bee file
    is saved.
    """

    TYPE = 'error'

    def __init__(self, text=None, **kwargs):
        super().__init__(text or "Text")
        self.original_save_id = None
        logger.debug(f'Initialized {self}')
        self.is_image = False
        self.init_selectable()
        self.is_editable = False
        self.setDefaultTextColor(QtGui.QColor(*COLORS['Scene:Text']))

    @classmethod
    def create_from_data(cls, **kwargs):
        data = kwargs.get('data', {})
        item = cls(**data)
        return item

    def __str__(self):
        txt = self.toPlainText()[:40]
        return (f'Error "{txt}"')

    def contains(self, point):
        return self.boundingRect().contains(point)

    def paint(self, painter, option, widget):
        painter.setPen(Qt.PenStyle.NoPen)
        color = QtGui.QColor(200, 0, 0)
        brush = QtGui.QBrush(color)
        painter.setBrush(brush)
        painter.drawRect(QtWidgets.QGraphicsTextItem.boundingRect(self))
        option.state = QtWidgets.QStyle.StateFlag.State_Enabled
        super().paint(painter, option, widget)
        self.paint_selectable(painter, option, widget)

    def update_from_data(self, **kwargs):
        self.original_save_id = kwargs.get('save_id', self.original_save_id)
        self.setPos(kwargs.get('x', self.pos().x()),
                    kwargs.get('y', self.pos().y()))
        self.setZValue(kwargs.get('z', self.zValue()))
        self.setScale(kwargs.get('scale', self.scale()))
        self.setRotation(kwargs.get('rotation', self.rotation()))

    def create_copy(self):
        item = BeeErrorItem(self.toPlainText())
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        return item

    def flip(self, *args, **kwargs):
        """Returns the flip value (1 or -1)"""
        # Never display error messages flipped
        return 1

    def do_flip(self, *args, **kwargs):
        """Flips the item."""
        # Never flip error messages
        pass

    def copy_to_clipboard(self, clipboard):
        clipboard.setText(self.toPlainText())
