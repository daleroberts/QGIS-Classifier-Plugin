__revision__ = '$Format:%H$'

import os
import sys
import inspect

from qgis.core import QgsProcessingAlgorithm, QgsApplication
from .ContourGeneratorProvider import ContourGeneratorProvider
from .ContourDialog import ContourDialogPlugin

cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]

if cmd_folder not in sys.path:
    sys.path.insert(0, cmd_folder)


class ContourPlugin(object):

    def __init__(self,iface):
        self.provider = ContourGeneratorProvider()
        self.dialog=ContourDialogPlugin(iface)

    def initGui(self):
        QgsApplication.processingRegistry().addProvider(self.provider)
        self.dialog.initGui()

    def unload(self):
        QgsApplication.processingRegistry().removeProvider(self.provider)
        self.dialog.unload()
