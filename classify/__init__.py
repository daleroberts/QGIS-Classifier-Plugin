# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    from .ClassifyPlugin import ClassifyPlugin
    return ClassifyPlugin(iface)