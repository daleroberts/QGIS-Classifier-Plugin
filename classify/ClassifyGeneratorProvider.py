from PyQt5.QtGui import QIcon
from qgis.core import QgsProcessingProvider
from .ClassifyGeneratorAlgorithm import ClassifyGeneratorAlgorithm
from . import resources


class ClassifyGeneratorProvider(QgsProcessingProvider):
    def __init__(self):
        QgsProcessingProvider.__init__(self)

        # Load algorithms
        self.alglist = [ClassifyGeneratorAlgorithm()]

    def unload(self):
        pass

    def loadAlgorithms(self):
        for alg in self.alglist:
            self.addAlgorithm(alg)

    def icon(self):
        return QIcon(":/plugins/classify/classify.png")

    def id(self):
        return "classifyplugin"

    def name(self):
        return self.tr("Classify plugin")

    def longName(self):
        return self.name()
