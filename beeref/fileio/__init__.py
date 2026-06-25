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

from PyQt6 import QtCore

from beeref import commands
from beeref.fileio.errors import BeeFileIOError
from beeref.fileio.image import load_image, source_for
from beeref.fileio.sql import SQLiteIO, is_bee_file
from beeref.items import BeePixmapItem


__all__ = [
    'is_bee_file',
    'load_bee',
    'save_bee',
    'load_images',
    'ThreadedLoader',
    'BeeFileIOError',
]

logger = logging.getLogger(__name__)


def load_bee(filename, scene, worker=None):
    """Load BeeRef native file."""
    logger.info(f'Loading from file {filename}...')
    io = SQLiteIO(filename, scene, readonly=True, worker=worker)
    return io.read()


def save_bee(filename, scene, create_new=False, worker=None):
    """Save BeeRef native file."""
    logger.info(f'Saving to file {filename}...')
    logger.debug(f'Create new: {create_new}')
    io = SQLiteIO(filename, scene, create_new, worker=worker)
    io.write()
    logger.info('End save')


def load_images(filenames, pos, scene, worker, fallback_image=None):
    """Add images to existing scene.

    ``fallback_image`` is an optional QImage to use when a single source
    can't be loaded. This covers dragging an image from a web browser,
    which often hands over a page link that can't be downloaded (for
    example because the site blocks it) together with the rendered image
    itself: if the link fails, the dragged image is used instead.
    """

    errors = []
    items = []
    worker.begin_processing.emit(len(filenames))
    for i, source in enumerate(filenames):
        logger.info(f'Loading image from file {source}')
        img, filename = load_image(source)
        worker.progress.emit(i)
        if img.isNull():
            if (len(filenames) == 1
                    and fallback_image is not None
                    and not fallback_image.isNull()):
                logger.info(f'Could not load {filename}; '
                            'using the dragged image instead')
                img = fallback_image
            else:
                logger.info(f'Could not load file {filename}')
                errors.append(filename)
                continue

        item = BeePixmapItem(img, filename)
        # Remember where the image came from, even when the link itself
        # couldn't be loaded and the dragged image was used instead.
        item.set_source(source_for(source))
        item.set_pos_center(pos)
        scene.add_item_later({'item': item, 'type': 'pixmap'}, selected=True)
        items.append(item)
        if worker.canceled:
            break
        # Give main thread time to process items:
        worker.msleep(10)

    scene.undo_stack.push(
        commands.InsertItems(scene, items, ignore_first_redo=True))
    worker.finished.emit('', errors)


class ThreadedIO(QtCore.QThread):
    """Dedicated thread for loading and saving."""

    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal(str, list)
    begin_processing = QtCore.pyqtSignal(int)
    user_input_required = QtCore.pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.kwargs['worker'] = self
        self.canceled = False

    def run(self):
        self.func(*self.args, **self.kwargs)

    def on_canceled(self):
        self.canceled = True
