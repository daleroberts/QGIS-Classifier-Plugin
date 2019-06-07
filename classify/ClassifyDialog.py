from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtXml import QDomDocument
from qgis.core import *
from qgis.gui import QgsMessageBar
from . import resources
from . import ClassifyMethod
from .ClassifyMethod import ClassifyMethodError
from .ClassifyGenerator import ClassifyGenerator, ClassifyType, ClassifyExtendOption
from .ClassifyGenerator import ClassifyError, ClassifyGenerationError

import sys
import os.path
import string
import math
import re
import inspect

mplAvailable = True
try:
    import numpy as np
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    from matplotlib.mlab import griddata
except ImportError:
    mplAvailable = False

from .ClassifyDialogUi import Ui_ClassifyDialog

EPSILON = 1.0e-27
LINES = "lines"
FILLED = "filled"
BOTH = "both"
LAYERS = "layer"


def tr(string):
    return QCoreApplication.translate("Processing", string)


class ClassifyDialogPlugin:
    def __init__(self, iface):
        self._iface = iface

    def initGui(self):
        if not mplAvailable:
            QMessageBox.warning(
                self._iface.mainWindow(),
                tr("Classify error"),
                tr(
                    "The Classify plugin is disabled as it requires python modules"
                    " numpy and matplotlib which are not both installed"
                ),
            )
            return

        self.action = QAction(
            QIcon(":/plugins/classify/classify.png"),
            "Classify",
            self._iface.mainWindow(),
        )
        self.action.setWhatsThis(tr("Generate Classifys based on point vector data"))
        self.action.triggered.connect(self.run)
        self._iface.addToolBarIcon(self.action)
        self._iface.rasterMenu().addAction(self.action)

    def unload(self):
        try:
            self._iface.removePluginMenu("&Classify", self.action)
            self._iface.rasterMenu().removeAction(self.action)
            self._iface.removeToolBarIcon(self.action)
        except:
            pass

    def run(self):
        try:
            dlg = ClassifyDialog(self._iface)
            dlg.exec_()
        except ClassifyError:
            QMessageBox.warning(
                self._iface.mainWindow(), tr("Classify error"), str(sys.exc_info()[1])
            )


class ClassifyDialog(QDialog, Ui_ClassifyDialog):
    class Feedback:
        def __init__(self, messagebar, progress):
            self._messageBar = messagebar
            self._progress = progress

        def isCanceled(self):
            return False

        def setProgress(self, percent):
            if self._progress:
                self._progress.setValue(percent)

        def pushInfo(self, info):
            self._messageBar.pushInfo("", info)

        def reportError(self, message, fatal=False):
            self._messageBar.pushWarning(
                tr("Error") if fatal else tr("Warning"), message
            )

    def __init__(self, iface):
        QDialog.__init__(self)
        self._iface = iface
        self._origin = None
        self._loadedDataDef = None
        self._layer = None
        self._zField = ""
        self._loadingLayer = False
        self._ClassifyId = ""
        self._replaceLayerSet = None
        self._canEditList = False

        # Set up the user interface from Designer.
        self.setupUi(self)

        self.uAddButton.setEnabled(False)
        # re = QRegExp("\\d+\\.?\\d*(?:[Ee][+-]?\\d+)?")
        self.uLevelsList.setSortingEnabled(False)
        self.uSourceLayer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.uNClassify.setMinimum(2)
        self.uNClassify.setValue(10)
        self.uSetMinimum.setChecked(False)
        self.uMinClassify.setEnabled(False)
        self.uSetMaximum.setChecked(False)
        self.uMaxClassify.setEnabled(False)
        self.uExtend.setCurrentIndex(0)
        self.uExtend.setEnabled(False)
        self.progressBar.setValue(0)
        for method in ClassifyMethod.methods:
            self.uMethod.addItem(method.name, method.id)
        for option in ClassifyExtendOption.options():
            self.uExtend.addItem(ClassifyExtendOption.description(option), option)

        self._feedback = ClassifyDialog.Feedback(self.uMessageBar, self.progressBar)
        self._generator = ClassifyGenerator(feedback=self._feedback)

        self.loadSettings()

        mapCanvas = self._iface.mapCanvas()
        self.enableClassifyParams()
        self.enableOkButton()

        # Signals
        self.uSourceLayer.layerChanged.connect(self.uSourceLayerChanged)
        self.uClassifyInterval.valueChanged[float].connect(self.computeLevels)
        self.uSetMinimum.toggled[bool].connect(self.toggleSetMinimum)
        self.uSetMaximum.toggled[bool].connect(self.toggleSetMaximum)
        self.uMinClassify.valueChanged[float].connect(self.computeLevels)
        self.uMaxClassify.valueChanged[float].connect(self.computeLevels)
        self.uNClassify.valueChanged[int].connect(self.computeLevels)
        self.uPrecision.valueChanged[int].connect(self.updatePrecision)
        self.uTrimZeros.toggled[bool].connect(self.updatePrecision)
        self.uLevelsList.itemClicked[QListWidgetItem].connect(self.editLevel)
        self.uHelpButton.clicked.connect(self.showHelp)
        self.uAddButton.clicked.connect(self.addClassifys)
        self.uCloseButton.clicked.connect(self.closeDialog)
        self.uMethod.currentIndexChanged[int].connect(self.computeLevels)
        self.uMethod.currentIndexChanged[int].connect(self.enableClassifyParams)
        self.uLayerClassifys.toggled[bool].connect(self.modeToggled)

        # populate layer list
        if self.uSourceLayer.count() <= 0:
            raise ClassifyError(tr("There are no layers suitable for classifying"))
        self.setupCurrentLayer(mapCanvas.currentLayer())
        if self.uSourceLayer.currentIndex() < 0 and self.uSourceLayer.count() == 1:
            self.uSourceLayer.setCurrentIndex(0)
        self.uSourceLayerChanged(self.uSourceLayer.currentLayer())

        # Is MPL version Ok?
        if self._isMPLOk() == False:
            self.warnUser(
                tr(
                    "You are using an old version matplotlib - only gridded data is supported"
                )
            )

    def warnUser(self, message):
        self._feedback.reportError(message)

    def adviseUser(self, message):
        self._feedback.pushInfo(message)

    def closeDialog(self):
        self.saveSettings()
        self.close()

    def _isMPLOk(self):
        """
        Check if matplotlib version > 1.0.0 for Classifying fonctions selection
        """
        version = [int(i) for i in mpl.__version__.split(".")[0:2]]
        return version >= [1, 0]

    def updatePrecision(self, ndp):
        self.setLabelFormat()
        ndp = self.uPrecision.value()
        if ndp < 0:
            ndp = 4
        self.uMinClassify.setDecimals(ndp)
        self.uMaxClassify.setDecimals(ndp)
        self.uClassifyInterval.setDecimals(ndp)
        self.uClassifyInterval.setDecimals(ndp)
        x, y, z = self._generator.data()
        if z is not None:
            if not self.uSetMinimum.isChecked():
                self.uMinClassify.setValue(np.min(z))
            if not self.uSetMaximum.isChecked():
                self.uMaxClassify.setValue(np.max(z))
            self.showLevels()

    def _getOptionalValue(self, properties, name, typefunc):
        fval = properties.get(name, "")
        if fval != "":
            try:
                return typefunc(fval)
            except:
                pass
        return None

    def setupCurrentLayer(self, layer):
        if not layer:
            return
        properties = self.getClassifyProperties(layer)
        ClassifyId = ""
        sourceLayer = None
        if properties:
            layerId = properties.get("SourceLayerId")
            for l in self.sourceLayers():
                if l.id() == layerId:
                    sourceLayer = l
                    break
            if sourceLayer:
                layer = sourceLayer
                ClassifyId = properties.get("ClassifyId")
        index = self.uSourceLayer.setLayer(layer)
        # If valid existing Classify layer, then reset
        if not ClassifyId:
            return
        layerSet = self.ClassifyLayerSet(ClassifyId)
        try:
            attr = properties.get("SourceLayerAttr")
            if FILLED in layerSet:
                pass
            elif LAYERS in layerSet:
                self.uLayerClassifys.setChecked(True)
            index = self.uMethod.findData(properties.get("Method"))
            if index >= 0:
                self.uMethod.setCurrentIndex(index)
            index = self.uExtend.findData(properties.get("Extend"))
            if index >= 0:
                self.uExtend.setCurrentIndex(index)
            self.uPrecision.setValue(int(properties.get("LabelPrecision")))
            self.uTrimZeros.setChecked(properties.get("TrimZeros") == "yes")
            self.uLabelUnits.setText(properties.get("LabelUnits") or "")
            self.uApplyColors.setChecked(properties.get("ApplyColors") == "yes")
            ramp = self.stringToColorRamp(properties.get("ColorRamp"))
            if ramp:
                self.uColorRamp.setColorRamp(ramp)
            self.uReverseRamp.setChecked(properties.get("ReverseRamp") == "yes")
            fval = self._getOptionalValue(properties, "MinClassify", float)
            self.uSetMinimum.setChecked(fval is not None)
            if fval is not None:
                self.uMinClassify.setValue(fval)
            fval = self._getOptionalValue(properties, "MaxClassify", float)
            self.uSetMaximum.setChecked(fval is not None)
            if fval is not None:
                self.uMaxClassify.setValue(fval)
            levels = properties.get("Levels").split(";")
            ival = self._getOptionalValue(properties, "NClassify", int)
            if ival is not None:
                self.uNClassify.setValue(ival)
            self.uLevelsList.clear()
            for level in levels:
                self.uLevelsList.addItem(level)
            fval = self._getOptionalValue(properties, "Interval", float)
            if fval is not None:
                self.uClassifyInterval.setValue(fval)
        finally:
            pass
        self._replaceLayerSet = layerSet

    def uSourceLayerChanged(self, layer):
        if self._loadingLayer:
            return
        self._replaceLayerSet = None
        self._layer = layer
        self._loadingLayer = False
        self.enableOkButton()

    def dataChanged(self):
        x, y, z = self._generator.data()
        if z is not None:
            zmin = np.min(z)
            zmax = np.max(z)
            ndp = self.uPrecision.value()
            if zmax - zmin > 0:
                ndp2 = ndp
                while 10 ** (-ndp2) > (zmax - zmin) / 100 and ndp2 < 10:
                    ndp2 += 1
                if ndp2 != ndp:
                    self.uPrecision.setValue(ndp2)
                    self.adviseUser(
                        tr(
                            "Resetting the label precision to match range of data values"
                        )
                    )
            if not self.uSetMinimum.isChecked():
                self.uMinClassify.setValue(zmin)
            if not self.uSetMaximum.isChecked():
                self.uMaxClassify.setValue(zmax)
            gridded = self._generator.isGridded()
            description = tr("Classifying {0} points").format(len(z))
            if gridshape is not None:
                description = description + tr(" in a {0} x {1} grid").format(
                    *gridshape
                )
            else:
                description = description + " (" + tr("not in regular grid") + ")"
        else:
            pass

    def reloadData(self):
        if self._loadingLayer:
            return
        self._loadingLayer = True
        try:
            fids = None
            self._generator.setDataSource(self._layer, self._zField, fids)
            duptol = 0.0
            self._generator.setDuplicatePointTolerance(duptol)
            self.dataChanged()
        finally:
            self._loadingLayer = False
        self._replaceLayerSet = None
        if not self._layer or not self._zField:
            self.enableOkButton()
            return
        self.computeLevels()
        self.updateOutputName()
        self.enableOkButton()

    def updateOutputName(self):
        if self._layer.name() and self._zField:
            zf = self._zField
            if re.search(r"\W", zf):
                zf = "expr"
            self.uOutputName.setText("%s_%s" % (self._layer.name(), zf))

    def editLevel(self, item=None):
        if not self._canEditList:
            return
        if item is None or QApplication.keyboardModifiers() & Qt.ShiftModifier:
            list = self.uLevelsList
            val = " ".join([list.item(i).text() for i in range(0, list.count())])
        else:
            val = item.text()
        newval, ok = QInputDialog.getText(
            self,
            tr("Update level"),
            tr("Enter a single level to replace this one")
            + "\n"
            + tr("or a space separated list of levels to replace all"),
            QLineEdit.Normal,
            val,
        )
        if ok:
            values = newval.split()
            fval = []
            for v in values:
                try:
                    fval.append(float(v))
                except:
                    QMessageBox.warning(
                        self._iface.mainWindow(),
                        tr("Classify error"),
                        tr("Invalid Classify value {0}").format(v),
                    )
                    return
            if len(values) < 1:
                return
            if len(values) == 1:
                item.setText(newval)
                self.enableOkButton()
            else:
                values.sort(key=float)
                index = self.uMethod.findData("manual")
                if index >= 0:
                    self.uMethod.setCurrentIndex(index)
                self.uNClassify.setValue(len(values))
                self.uLevelsList.clear()
                for v in values:
                    self.uLevelsList.addItem(v)

            fval = self.getLevels()
            self._generator.setClassifyLevels(fval)
            self.enableOkButton()

    def getMethod(self):
        index = self.uMethod.currentIndex()
        methodid = self.uMethod.itemData(index)
        return ClassifyMethod.getMethod(methodid)

    def ClassifyLevelParams(self):
        method = self.getMethod()
        nClassify = self.uNClassify.value()
        interval = self.uClassifyInterval.value()
        zmin = None
        zmax = None
        if self.uSetMinimum.isChecked():
            zmin = self.uMinClassify.value()
        if self.uSetMaximum.isChecked():
            zmax = self.uMaxClassify.value()
        list = self.uLevelsList
        levels = " ".join([list.item(i).text() for i in range(0, list.count())])
        params = {
            "min": zmin,
            "max": zmax,
            "interval": interval,
            "nClassify": nClassify,
            "maxClassify": nClassify,
            "mantissa": None,
            "levels": levels,
        }
        return method.id, params

    def enableClassifyParams(self):
        method = self.getMethod()
        params = []
        if method is not None:
            params = list(method.required)
            params.extend(method.optional)
        self.uClassifyInterval.setEnabled("interval" in params)
        self.uNClassify.setEnabled("nClassify" in params or "maxClassify" in params)
        self.uSetMinimum.setEnabled("min" in params)
        self.uMinClassify.setEnabled("min" in params and self.uSetMinimum.isChecked())
        self.uSetMaximum.setEnabled("max" in params)
        self.uMaxClassify.setEnabled("max" in params and self.uSetMaximum.isChecked())
        self._canEditList = "levels" in params

    def toggleSetMinimum(self):
        self.uMinClassify.setEnabled(self.uSetMinimum.isChecked())
        if not self.uSetMinimum.isChecked():
            x, y, z = self._generator.data()
            if z is not None:
                self.uMinClassify.setValue(np.min(z))
                self.computeLevels()

    def toggleSetMaximum(self):
        self.uMaxClassify.setEnabled(self.uSetMaximum.isChecked())
        if not self.uSetMaximum.isChecked():
            x, y, z = self._generator.data()
            if z is not None:
                self.uMaxClassify.setValue(np.max(z))
                self.computeLevels()

    def computeLevels(self):
        # Use ClassifyGenerator code
        methodcode, params = self.ClassifyLevelParams()
        self._generator.setClassifyMethod(methodcode, params)
        self.showLevels()
        self.enableOkButton()

    def showLevels(self):
        self.uLevelsList.clear()
        try:
            levels = self._generator.levels()
            # Need to create some Classifys if manual and none
            # defined
            if self._canEditList and len(levels) == 0:
                x, y, z = self._generator.data()
                if z is not None:
                    nClassify = self.uNClassify.value()
                    try:
                        levels = ClassifyMethod.calculateLevels(
                            z, "equal", nClassify=nClassify
                        )
                    except:
                        levels = [0.0]
        except (ClassifyMethodError, ClassifyError) as ex:
            self._feedback.pushInfo(ex.message())
            return
        for i in range(0, len(levels)):
            self.uLevelsList.addItem(self.formatLevel(levels[i]))

    def modeToggled(self, enabled):
        if enabled:
            self.enableOkButton()

    def enableOkButton(self):
        self.uAddButton.setEnabled(False)
        try:
            self.validate()
            self.uAddButton.setEnabled(True)
        except:
            pass

    def confirmReplaceSet(self, set):
        message = (
            tr("The following layers already have Classifys of {0}").format(
                self._zField
            )
            + "\n"
            + tr("Do you want to replace them with the new Classifys?")
            + "\n\n"
        )

        for layer in list(set.values()):
            message = message + "\n   " + layer.name()
        return QMessageBox.question(
            self,
            tr("Replace Classify layers"),
            message,
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )

    def addClassifys(self):
        try:
            self.validate()
            self._ClassifyId = QDateTime.currentDateTime().toString("yyyyMMddhhmmss")
            replaceClassifyId = ""
            for set in self.candidateReplacementSets():
                result = self.confirmReplaceSet(set)
                if result == QMessageBox.Cancel:
                    return
                if result == QMessageBox.Yes:
                    self._replaceLayerSet = set
                    replaceClassifyId = self.layerSetClassifyId(set)
                    break
            try:
                QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
                self.setLabelFormat()
                if self.uLayerClassifys.isChecked():
                    self.makeClassifyLayer(ClassifyType.layer)
                oldLayerSet = self.ClassifyLayerSet(replaceClassifyId)
                if oldLayerSet:
                    for layer in list(oldLayerSet.values()):
                        QgsProject.instance().removeMapLayer(layer.id())
                self._replaceLayerSet = self.ClassifyLayerSet(self._ClassifyId)
            finally:
                QApplication.restoreOverrideCursor()

        except ClassifyGenerationError as cge:
            self.warnUser(
                tr("Exception encountered: ")
                + str(cge)
                + " "
                + tr("(Try removing duplicate points)")
            )
        except ClassifyError as ce:
            self.warnUser(tr("Error calculating grid/Classifys: {0}").format(ce))
        # self.uAddButton.setEnabled(False)

    def showHelp(self):
        file = os.path.realpath(__file__)
        file = os.path.join(os.path.dirname(file), "doc", "ClassifyDialog.html")
        QDesktopServices.openUrl(QUrl.fromLocalFile(file))

    def validate(self):
        message = None
        if self.uSourceLayer.currentLayer() is None:
            message = tr("Please specify raster layer")
        if message != None:
            raise ClassifyError(message)

    def sourceLayers(self):
        for layer in list(QgsProject.instance().mapLayers().values()):
            if layer.type() == layer.RasterLayer:
                yield layer

    def getLevels(self):
        list = self.uLevelsList
        return [float(list.item(i).text()) for i in range(0, list.count())]

    def clearLayer(self, layer):
        pl = layer.dataProvider()
        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes([])
        fids = []
        for f in pl.getFeatures(request):
            fids.append(f.id())
        pl.deleteFeatures(fids)
        pl.deleteAttributes(pl.attributeIndexes())
        layer.updateFields()

    def createVectorLayer(self, type, name, mode, fields, crs):
        layer = None
        if self._replaceLayerSet:
            layer = self._replaceLayerSet.get(mode)

        if layer:
            self.clearLayer(layer)
        else:
            url = QgsWkbTypes.displayString(type) + "?crs=internal:" + str(crs.srsid())
            layer = QgsVectorLayer(url, name, "memory")

        if layer is None:
            raise ClassifyError(tr("Could not create layer for Classifys"))

        pr = layer.dataProvider()
        pr.addAttributes(fields)
        layer.updateFields()

        layer.setCrs(crs, False)
        levels = ";".join(map(str, self.getLevels()))
        properties = {
            "ClassifyId": self._ClassifyId,
            "SourceLayerId": self._layer.id(),
            "SourceLayerAttr": self._zField,
            "Mode": mode,
            "Levels": levels,
            "LabelPrecision": str(self.uPrecision.value()),
            "TrimZeros": "yes" if self.uTrimZeros.isChecked() else "no",
            "LabelUnits": str(self.uLabelUnits.text()),
            "NClassify": str(self.uNClassify.value()),
            "MinClassify": str(self.uMinClassify.value())
            if self.uSetMinimum.isChecked()
            else "",
            "MaxClassify": str(self.uMaxClassify.value())
            if self.uSetMaximum.isChecked()
            else "",
            "Extend": self.uExtend.itemData(self.uExtend.currentIndex()),
            "Method": self.uMethod.itemData(self.uMethod.currentIndex()),
            "ApplyColors": "yes" if self.uApplyColors.isChecked() else "no",
            "ColorRamp": self.colorRampToString(self.uColorRamp.colorRamp()),
            "ReverseRamp": "yes" if self.uReverseRamp.isChecked() else "no",
            "ClassifyInterval": str(self.uClassifyInterval.value()),
        }
        self.setClassifyProperties(layer, properties)
        return layer

    def addLayer(self, layer):
        registry = QgsProject.instance()
        if not registry.mapLayer(layer.id()):
            registry.addMapLayer(layer)
        else:
            node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
            if node is not None:
                node.setItemVisibilityChecked(True)
            layer.triggerRepaint()
            self._iface.mapCanvas().refresh()

    def setClassifyProperties(self, layer, properties):
        for key in list(properties.keys()):
            layer.setCustomProperty("ClassifyPlugin." + key, properties[key])

    def getClassifyProperties(self, layer):
        if layer.type() != layer.RasterLayer or layer.dataProvider().name() != "memory":
            return None
        properties = {}
        for key in [
            "ClassifyId",
            "SourceLayerId",
            "SourceLayerAttr",
            "Mode",
            "Levels",
            "LabelPrecision",
            "MinClassify",
            "MaxClassify",
            "Extend",
            "Method",
            "ApplyColors",
            "ColorRamp",
            "ReverseRamp",
        ]:
            properties[key] = str(layer.customProperty("ClassifyPlugin." + key))
        if not properties["ClassifyId"]:
            return None
        return properties

    def ClassifyLayers(self, wanted={}):
        for layer in list(QgsProject.instance().mapLayers().values()):
            properties = self.getClassifyProperties(layer)
            if not properties:
                continue
            ok = True
            for key in list(wanted.keys()):
                if properties.get(key) != wanted[key]:
                    ok = False
                    break
            if ok:
                yield layer

    def ClassifyLayerSet(self, ClassifyId):
        layers = self.ClassifyLayers({"ClassifyId": ClassifyId})
        layerSet = {}
        for layer in layers:
            properties = self.getClassifyProperties(layer)
            layerSet[properties.get("Mode")] = layer
        return layerSet

    def layerSetClassifyId(self, layerSet):
        if layerSet:
            return self.getClassifyProperties(list(layerSet.values())[0]).get(
                "ClassifyId"
            )
        return None

    def candidateReplacementSets(self):
        # Note: use _replaceLayerSet first as this will be the layer
        # set that the Classify dialog was opened with. Following this
        # look for any other potential layers.
        ids = []
        if self._replaceLayerSet:
            set = self._replaceLayerSet
            self._replaceLayerSet = None
            ids.append(self.layerSetClassifyId(set))
            yield set

        for layer in self.ClassifyLayers(
            {"SourceLayerId": self._layer.id(), "SourceLayerAttr": self._zField}
        ):
            id = self.getClassifyProperties(layer).get("ClassifyId")
            if id in ids:
                continue
            ids.append(id)
            yield self.ClassifyLayerSet(id)

    def makeClassifyLayer(self, ctype):
        try:
            self._generator.setClassifyType(ctype)
            extend = self.uExtend.itemData(self.uExtend.currentIndex())
            self._generator.setClassifyExtendOption(extend)
            name = self.uOutputName.text()
            fields = self._generator.fields()
            geomtype = self._generator.wkbtype()
            crs = self._generator.crs()
            vl = self.createVectorLayer(geomtype, name, ctype, fields, crs)
            levels = []
            vl.startEditing()
            for feature in self._generator.ClassifyFeatures():
                vl.addFeature(feature)
                levels.append((feature["index"], feature["label"]))
            vl.updateExtents()
            vl.commitChanges()
        except (ClassifyError, ClassifyMethodError) as ex:
            self.warnUser(ex.message())
            return
        try:
            if len(levels) > 0:
                rendtype = "line" if ctype == ClassifyType.line else "polygon"
                self.applyRenderer(vl, rendtype, levels)
        except:
            self.warnUser("Error rendering Classify layer")
        self.addLayer(vl)
        self.adviseUser(tr("Classify layer {0} created").format(vl.name()))

    def dataChanged(self):
        x, y, z = self._generator.data()
        if z is not None:
            zmin = np.min(z)
            zmax = np.max(z)
            ndp = self.uPrecision.value()
            if zmax - zmin > 0:
                ndp2 = ndp
                while 10 ** (-ndp2) > (zmax - zmin) / 100 and ndp2 < 10:
                    ndp2 += 1
                if ndp2 != ndp:
                    self.uPrecision.setValue(ndp2)
                    self.adviseUser(
                        tr(
                            "Resetting the label precision to match range of data values"
                        )
                    )
            if not self.uSetMinimum.isChecked():
                self.uMinClassify.setValue(zmin)
            if not self.uSetMaximum.isChecked():
                self.uMaxClassify.setValue(zmax)
            gridded = self._generator.isGridded()
            description = "Classifying {0} points".format(len(z))
            if gridded:
                gridshape = self._generator.gridShape()
                description = description + " in a {0} x {1} grid".format(*gridshape)
            else:
                description = description + " (not in regular grid)"
        #            self.uLayerDescription.setText(description)
        else:
            #            self.uLayerDescription.setText(tr("No data selected for Classifying"))
            pass

    def setLabelFormat(self):
        ndp = self.uPrecision.value()
        trim = self.uTrimZeros.isChecked()
        units = self.uLabelUnits.text()
        self._generator.setLabelFormat(ndp, trim, units)

    def formatLevel(self, level):
        return self._generator.formatLevel(level)

    def applyRenderer(self, layer, type, levels):
        if not self.uApplyColors.isChecked():
            return
        ramp = self.uColorRamp.colorRamp()
        reversed = self.uReverseRamp.isChecked()
        if ramp is None:
            return
        nLevels = len(levels)
        if nLevels < 2:
            return
        renderer = QgsCategorizedSymbolRenderer("index")
        for i, level in enumerate(levels):
            value, label = level
            rampvalue = float(i) / (nLevels - 1)
            if reversed:
                rampvalue = 1.0 - rampvalue
            color = ramp.color(rampvalue)
            symbol = None
            if type == "line":
                symbol = QgsLineSymbol.createSimple({})
            else:
                symbol = QgsFillSymbol.createSimple({"outline_style": "no"})
            symbol.setColor(color)
            category = QgsRendererCategory(value, symbol, label)
            renderer.addCategory(category)
        layer.setRenderer(renderer)

    def colorRampToString(self, ramp):
        if ramp is None:
            return ""
        d = QDomDocument()
        d.appendChild(QgsSymbolLayerUtils.saveColorRamp("ramp", ramp, d))
        rampdef = d.toString()
        return rampdef

    def stringToColorRamp(self, rampdef):
        try:
            if "<" not in rampdef:
                return None
            d = QDomDocument()
            d.setContent(rampdef)
            return QgsSymbolLayerUtils.loadColorRamp(d.documentElement())
        except:
            return None

    def saveSettings(self):
        settings = QSettings()
        base = "/plugins/Classify/"
        mode = LAYERS if self.uLayerClassifys.isChecked() else LINES
        list = self.uLevelsList
        values = " ".join([list.item(i).text() for i in range(0, list.count())])
        settings.setValue(base + "mode", mode)
        settings.setValue(base + "levels", str(self.uNClassify.value()))
        settings.setValue(base + "values", values)
        settings.setValue(base + "interval", str(self.uClassifyInterval.value()))
        settings.setValue(
            base + "extend", self.uExtend.itemData(self.uExtend.currentIndex())
        )
        settings.setValue(
            base + "method", self.uMethod.itemData(self.uMethod.currentIndex())
        )
        settings.setValue(base + "precision", str(self.uPrecision.value()))
        settings.setValue(
            base + "setmin", "yes" if self.uSetMinimum.isChecked() else "no"
        )
        settings.setValue(base + "minval", str(self.uMinClassify.value()))
        settings.setValue(
            base + "setmax", "yes" if self.uSetMaximum.isChecked() else "no"
        )
        settings.setValue(base + "maxval", str(self.uMaxClassify.value()))
        settings.setValue(
            base + "trimZeros", "yes" if self.uTrimZeros.isChecked() else "no"
        )
        settings.setValue(base + "units", self.uLabelUnits.text())
        settings.setValue(
            base + "applyColors", "yes" if self.uApplyColors.isChecked() else "no"
        )
        settings.setValue(
            base + "ramp", self.colorRampToString(self.uColorRamp.colorRamp())
        )
        settings.setValue(
            base + "reverseRamp", "yes" if self.uReverseRamp.isChecked() else "no"
        )
        settings.setValue(base + "dialogWidth", str(self.width()))
        settings.setValue(base + "dialogHeight", str(self.height()))

    def loadSettings(self):
        settings = QSettings()
        base = "/plugins/Classify/"
        try:
            mode = settings.value(base + "mode")
            if mode == LAYERS:
                self.uLayerClassifys.setChecked(True)

            levels = settings.value(base + "levels")
            if levels is not None and levels.isdigit():
                self.uNClassify.setValue(int(levels))

            values = settings.value(base + "values")
            if values is not None:
                self.uLevelsList.clear()
                for value in values.split():
                    self.uLevelsList.addItem(value)

            setmin = settings.value(base + "setmin") == "yes"
            self.uSetMinimum.setChecked(setmin)
            if setmin:
                try:
                    value = settings.value(base + "minval")
                    self.uMinClassify.setValue(float(value))
                except:
                    pass

            setmax = settings.value(base + "setmax") == "yes"
            self.uSetMaximum.setChecked(setmax)
            if setmax:
                try:
                    value = settings.value(base + "maxval")
                    self.uMaxClassify.setValue(float(value))
                except:
                    pass

            extend = settings.value(base + "extend")
            index = self.uExtend.findData(extend)
            if index >= 0:
                self.uExtend.setCurrentIndex(index)

            method = settings.value(base + "method")
            index = self.uMethod.findData(method)
            if index >= 0:
                self.uMethod.setCurrentIndex(index)

            precision = settings.value(base + "precision")
            if precision is not None and precision.isdigit():
                ndp = int(precision)
                self.uPrecision.setValue(ndp)
                self.uMinClassify.setDecimals(ndp)
                self.uMaxClassify.setDecimals(ndp)

            units = settings.value(base + "units")
            if units is not None:
                self.uLabelUnits.setText(units)

            applyColors = settings.value(base + "applyColors")
            self.uApplyColors.setChecked(applyColors == "yes")

            ramp = settings.value(base + "ramp")
            ramp = self.stringToColorRamp(ramp)
            if ramp:
                self.uColorRamp.setColorRamp(ramp)

            reverseRamp = settings.value(base + "reverseRamp")
            self.uReverseRamp.setChecked(reverseRamp == "yes")

            trimZeros = settings.value(base + "trimZeros")
            self.uTrimZeros.setChecked(trimZeros == "yes")

            width = settings.value(base + "dialogWidth")
            height = settings.value(base + "dialogHeight")
            if width is not None and height is not None:
                try:
                    width = int(width)
                    height = int(height)
                    self.resize(width, height)
                except:
                    pass
        except:
            pass
