"""Survey analysis, ported from https://github.com/hwbuehler/hgsfp-survey-tool.

The original ``survey_analyzer.py`` computed statistics and drew PDFs in one
class.  Here the computation lives on its own and the presentation layer is
HTML + CSS, shared by the browser preview and the PDF export.
"""
