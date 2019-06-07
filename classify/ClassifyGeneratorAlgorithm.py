__revision__ = "$Format:%H$"

import os.path
from PyQt5.QtCore import QCoreApplication, QUrl
from PyQt5.QtGui import QIcon
from qgis.core import (
    QgsProcessing,
    QgsFeatureSink,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExpression,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsWkbTypes,
)
from .ClassifyGenerator import ClassifyGenerator, ClassifyType, ClassifyExtendOption
from .ClassifyGenerator import ClassifyError, ClassifyMethodError
from . import ClassifyMethod
from . import resources


def tr(string):
    return QCoreApplication.translate("Processing", string)


class ClassifyGeneratorAlgorithmError(RuntimeError):
    pass


class ClassifyGeneratorAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm to calculate Classify lines or filled Classifys from
    attribute values of a point data layer.  
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    PrmOutputLayer = "OutputLayer"
    PrmInputLayer = "InputLayer"
    PrmInputField = "InputField"
    PrmClassifyMethod = "ClassifyMethod"
    PrmNClassify = "NClassify"
    PrmMinClassifyValue = "MinClassifyValue"
    PrmMaxClassifyValue = "MaxClassifyValue"
    PrmClassifyInterval = "ClassifyInterval"
    PrmClassifyLevels = "ClassifyLevels"
    PrmClassifyType = "ClassifyType"
    PrmExtendClassify = "ExtendOption"
    PrmLabelDecimalPlaces = "LabelDecimalPlaces"
    PrmLabelTrimZeros = "LabelTrimZeros"
    PrmLabelUnits = "LabelUnits"
    PrmDuplicatePointTolerance = "DuplicatePointTolerance"

    TypeValues = ClassifyType.types()
    TypeOptions = [ClassifyType.description(t) for t in TypeValues]

    ExtendValues = ClassifyExtendOption.options()
    ExtendOptions = [ClassifyExtendOption.description(t) for t in ExtendValues]

    MethodValues = [m.id for m in ClassifyMethod.methods]
    MethodOptions = [m.name for m in ClassifyMethod.methods]

    EnumMapping = {
        PrmClassifyMethod: (MethodValues, MethodOptions),
        PrmClassifyType: (TypeValues, TypeOptions),
        PrmExtendClassify: (ExtendValues, ExtendOptions),
    }

    def _enumParameter(self, name, description, optional=True):
        values, options = self.EnumMapping[name]
        return QgsProcessingParameterEnum(name, description, options, optional=optional)

    def _getEnumValue(self, parameters, name, context):
        # Wishful thinking - currently enum parameter can only accept integer value :-(
        # Hopefully will be able to get value as code in the future
        values, options = self.EnumMapping[name]
        if name not in parameters:
            return values[0]
        id = self.parameterAsString(parameters, name, context)
        if id not in values:
            try:
                id = values[int(id)]
            except:
                raise ClassifyGeneratorAlgorithmError(
                    tr("Invalid value {0} for {1}").format(id, name)
                )
        return id

    def initAlgorithm(self, config):
        """
        Set up parameters for the ClassifyGenerator algorithm
        """
        # Would be cleaner to create a widget, at least for the Classify levels.

        # Add the input point vector features source.
        # geometry.

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PrmInputLayer,
                tr("Input point layer"),
                [QgsProcessing.TypeVectorPoint],
            )
        )

        # Define the field/expression to Classify

        self.addParameter(
            QgsProcessingParameterExpression(
                self.PrmInputField,
                tr("Value to Classify"),
                parentLayerParameterName=self.PrmInputLayer,
            )
        )

        # Duplicate point radius - discards points if closer than
        # this to each other (approximately).  0 means don't discard

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmDuplicatePointTolerance,
                tr("Duplicate point tolerance"),
                QgsProcessingParameterNumber.Double,
                minValue=0.0,
                defaultValue=0.0,
                optional=True,
            )
        )

        # Define the Classify type

        self.addParameter(self._enumParameter(self.PrmClassifyType, tr("Classify type")))

        self.addParameter(
            self._enumParameter(
                self.PrmExtendClassify,
                tr("Extend filled Classify options"),
                optional=True,
            )
        )

        # Define the Classify level calculation method

        self.addParameter(
            self._enumParameter(
                self.PrmClassifyMethod, tr("Method used to calculate the Classify levels")
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmNClassify,
                tr("Number (or max number) of Classifys"),
                defaultValue=20,
                minValue=1,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmMinClassifyValue,
                tr("Minimum Classify level (omit to use data minimum)"),
                type=QgsProcessingParameterNumber.Double,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmMaxClassifyValue,
                tr("Maximum Classify level (omit to use data maximum)"),
                type=QgsProcessingParameterNumber.Double,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmClassifyInterval,
                tr("Classify interval"),
                QgsProcessingParameterNumber.Double,
                minValue=0.0,
                defaultValue=1.0,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PrmClassifyLevels,
                tr("User selected Classify levels"),
                multiLine=True,
                optional=True,
            )
        )

        # Define label formatting - number of significant digits and
        # whether trailiing zeros are trimmed.

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PrmLabelDecimalPlaces,
                tr("Label decimal places (-1 for auto)"),
                QgsProcessingParameterNumber.Integer,
                defaultValue=-1,
                minValue=-1,
                maxValue=10,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PrmLabelTrimZeros,
                tr("Trim trailing zeros from labels"),
                False,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PrmLabelUnits,
                tr("Units to append to label values"),
                "",
                optional=True,
            )
        )

        # Output layer for the Classifys

        self.addParameter(
            QgsProcessingParameterFeatureSink(self.PrmOutputLayer, tr("Output layer"))
        )

    def processAlgorithm(self, parameters, context, feedback):

        # Retrieve the Classify parameters

        source = self.parameterAsSource(parameters, self.PrmInputLayer, context)
        field = self.parameterAsExpression(parameters, self.PrmInputField, context)
        DuplicatePointTolerance = self.parameterAsDouble(
            parameters, self.PrmDuplicatePointTolerance, context
        )

        method = self._getEnumValue(parameters, self.PrmClassifyMethod, context)

        nClassify = self.parameterAsInt(parameters, self.PrmNClassify, context)
        zmin = None
        zmax = None
        if parameters[self.PrmMinClassifyValue] is not None:
            zmin = self.parameterAsDouble(parameters, self.PrmMinClassifyValue, context)
        if parameters[self.PrmMaxClassifyValue] is not None:
            zmax = self.parameterAsDouble(parameters, self.PrmMaxClassifyValue, context)
        interval = self.parameterAsDouble(parameters, self.PrmClassifyInterval, context)
        levels = self.parameterAsString(parameters, self.PrmClassifyLevels, context)

        Classifytype = self._getEnumValue(parameters, self.PrmClassifyType, context)
        extend = self._getEnumValue(parameters, self.PrmExtendClassify, context)
        labelndp = self.parameterAsInt(parameters, self.PrmLabelDecimalPlaces, context)
        labeltrim = self.parameterAsBool(parameters, self.PrmLabelTrimZeros, context)
        labelunits = self.parameterAsString(parameters, self.PrmLabelUnits, context)

        # Construct and configure the Classify generator

        params = {
            "min": zmin,
            "max": zmax,
            "nClassify": nClassify,
            "maxClassify": nClassify,
            "interval": interval,
            "levels": levels,
        }

        generator = ClassifyGenerator(source, field, feedback)
        generator.setDuplicatePointTolerance(DuplicatePointTolerance)
        generator.setClassifyMethod(method, params)
        generator.setClassifyType(Classifytype)
        generator.setClassifyExtendOption(extend)
        generator.setLabelFormat(labelndp, labeltrim, labelunits)

        # Create the destination layer

        dest_id = None
        try:
            wkbtype = generator.wkbtype()
            fields = generator.fields()
            crs = generator.crs()

            (sink, dest_id) = self.parameterAsSink(
                parameters, self.PrmOutputLayer, context, fields, wkbtype, crs
            )

            # Add features to the sink
            for feature in generator.ClassifyFeatures():
                sink.addFeature(feature, QgsFeatureSink.FastInsert)

        except (ClassifyError, ClassifyMethodError) as ex:
            feedback.reportError(ex.message())

        return {self.PrmOutputLayer: dest_id}

    def icon(self):
        return QIcon(":/plugins/Classify/Classify.png")

    def name(self):
        return "generateClassifys"

    def helpUrl(self):
        file = os.path.realpath(__file__)
        file = os.path.join(
            os.path.dirname(file), "doc", "ClassifyGeneratorAlgorithm.html"
        )
        if not os.path.exists(file):
            return ""
        return QUrl.fromLocalFile(file).toString(QUrl.FullyEncoded)

    def displayName(self):
        return tr("Generate Classifys")

    def shortHelpString(self):
        file = os.path.realpath(__file__)
        file = os.path.join(os.path.dirname(file), "ClassifyGeneratorAlgorithm.help")
        if not os.path.exists(file):
            return ""
        with open(file) as helpf:
            help = helpf.read()
        return help

    def group(self):
        return tr("Classifying")

    def groupId(self):
        return "Classifying"

    def createInstance(self):
        return ClassifyGeneratorAlgorithm()
