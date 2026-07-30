# Release migration review

The release tree was built in a clean clone of `myc0576/MycEvo`, not in the
protected source workspace. A default-deny public-file manifest selected 181
files. Private `.omx` state, ResearchLoop private content, local paths,
caches, logs, credentials, PDFs and other generated artifacts were excluded.

The source workspace retains its original remote and working-tree changes.
Only the clean release clone may be committed or pushed.
