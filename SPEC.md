# Trimarr specification

- Trimarr should download mkvmerge if its not present using the default path specified by the cli options
- Trimarr should ALWAYS err on the side of caution, if a file has been processed then check exit codes to ensure the processed file is ok BEFORE replacing the original (assuming not running in dry run mode)
