__revision__ = "$Format:%H$"

import os
import sys
import inspect

from qgis.core import QgsProcessingAlgorithm, QgsApplication
from .ClassifyGeneratorProvider import ClassifyGeneratorProvider
from .ClassifyDialog import ClassifyDialogPlugin

cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]

if cmd_folder not in sys.path:
    sys.path.insert(0, cmd_folder)


class ClassifyPlugin(object):
    def __init__(self, iface):
        self.provider = ClassifyGeneratorProvider()
        self.dialog = ClassifyDialogPlugin(iface)

    def initGui(self):
        QgsApplication.processingRegistry().addProvider(self.provider)
        self.dialog.initGui()

    def unload(self):
        QgsApplication.processingRegistry().removeProvider(self.provider)
        self.dialog.unload()
