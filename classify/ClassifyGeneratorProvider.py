from PyQt5.QtGui import QIcon
from qgis.core import QgsProcessingProvider
from .ContourGeneratorAlgorithm import ContourGeneratorAlgorithm
from . import resources

class ContourGeneratorProvider(QgsProcessingProvider):

    def __init__(self):
        QgsProcessingProvider.__init__(self)

        # Load algorithms
        self.alglist = [ContourGeneratorAlgorithm()]

    def unload(self):
        """
        Unloads the provider. Any tear-down steps required by the provider
        should be implemented here.
        """
        pass

    def loadAlgorithms(self):
        """
        Loads all algorithms belonging to this provider.
        """
        for alg in self.alglist:
            self.addAlgorithm( alg )

    def icon(self):
        return QIcon(":/plugins/contour/contour.png")

    def id(self):
        """
        Returns the unique provider id, used for identifying the provider. This
        string should be a unique, short, character only string, eg "qgis" or
        "gdal". This string should not be localised.
        """
        return 'contourplugin'

    def name(self):
        """
        Returns the provider name, which is used to describe the provider
        within the GUI.

        This string should be short (e.g. "Lastools") and localised.
        """
        return self.tr('Contour plugin')

    def longName(self):
        """
        Returns the a longer version of the provider name, which can include
        extra details such as version numbers. E.g. "Lastools LIDAR tools
        (version 2.2.1)". This string should be localised. The default
        implementation returns the same string as name().
        """
        return self.name()
