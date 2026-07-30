# Workspace manifests

Each A/B workspace owns a sibling directory such as
`workspaces/teaching-prep/` or `workspaces/class-teacher/` and exports a
default `WorkspaceManifest` from `manifest.ts`. The shared registry discovers
those files at build time, validates all manifests before filtering disabled
modules, and adds only enabled modules to navigation and routing.

F0 intentionally ships no enabled business manifest. The A and B lines add
their own lazy page and feature flags without editing shared navigation files.
