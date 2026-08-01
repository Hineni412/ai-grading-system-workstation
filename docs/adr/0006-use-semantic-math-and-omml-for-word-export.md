---
status: accepted
---

# Use semantic math and OMML for Word export

Imported questions retain source regions and a versioned mathematical expression containing restricted LaTeX, a validated semantic form, Word OMML, recognition metadata, and an original-image fallback. LaTeX is the editable interchange language; OMML is the primary DOCX representation so formulas remain editable in Word-compatible software. Original trusted DOCX math may be preserved, while scanned or normalized content is rebuilt through the shared renderer.

Ordinary paper export and personalized-paper export use the same question renderer and style profile. Turning all formulas into PNG, executing arbitrary recognized LaTeX, or maintaining separate renderers for each product were rejected because those choices make formulas uneditable, create a code-execution boundary, and multiply layout drift across one-paper-per-person output.
