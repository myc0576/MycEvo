# PaperFrames adapter contract

Adapters consume a declared input manifest and write only declared outputs under
`build/`. They must emit versioned JSON, checksums, provenance, and an explicit
`candidate` or `executed_unverified` status. They never promote scientific claims.
