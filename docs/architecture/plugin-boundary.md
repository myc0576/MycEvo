# Plugin boundary

MycEvo Core treats parsers, renderers, figure engines, bibliography tools, and
other PaperFrames integrations as external plugins. They are discovered through
the public `mycevo.plugins` entry-point group and described by
`mycevo.plugin_manifest.v1`.

Core may inspect and validate a manifest, create a plan, calculate a digest, and
verify a result. Core must not import a plugin implementation or execute plugin
code in its own process. Execution belongs to the separately versioned
`mycevo-runner` protocol, which receives an explicit workspace and capability
allowlist.

Every plugin declares read/write globs, network permission, subprocess names,
outputs, runtime, version, and (when installed) a content digest. A planned
plugin may publish its contract and fixtures without being executable.

The initial PaperFrames reference path is an external plugin composed of
Docling parsing, PaperFrames IR normalization, and Typst rendering. Later
adapters are intentionally separate: GROBID, Matplotlib, PyVista, ParaView,
FreeCAD, LaTeX, and bibliography services must not become Core dependencies.
