# paperframes-reference

Reference PaperFrames plugin contract for the M1 Docling → PaperFrames IR →
Typst path. It is intentionally outside `src/mycevo`; MycEvo Core does not
import this implementation.

Runtime requirements are optional external dependencies: `docling` and the
Typst compiler. If either is missing, the runner must return a structured
`missing_runtime_dependency` receipt and must not claim execution success.
