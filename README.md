# Altium iBOM

Experimental Altium-to-InteractiveHtmlBom converter.

This uses [`altium_monkey`](https://github.com/wavenumber-eng/altium_monkey) to parse Altium projects and emits the InteractiveHtmlBom generic JSON format. It can optionally call InteractiveHtmlBom to produce the final standalone HTML.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you are developing against local clones instead of installed packages, place them next to this repo:

```text
parent/
  Altium_iBOM/
  altium_monkey/
  interactivehtmlbom/
```

or set:

```powershell
$env:ALTIUM_MONKEY_PATH="C:\path\to\altium_monkey\src\py"
$env:INTERACTIVEHTMLBOM_PATH="C:\path\to\interactivehtmlbom"
```

## Usage

Generate iBOM generic JSON:

```powershell
.\.venv\Scripts\python.exe .\altium_to_ibom.py "C:\project\Board.PrjPcb" -o .\board.ibom-input.json
```

Generate JSON and HTML:

```powershell
.\.venv\Scripts\python.exe .\altium_to_ibom.py "C:\project\Board.PrjPcb" -o .\board.ibom-input.json --html --html-name board.ibom
```

## Current Coverage

The converter currently handles:

- component metadata and BOM fields
- component pads, including pin-1 markers
- free through-hole pads as via-style board features
- copper tracks, arcs, vias, and zones
- board outline segments and arc corners
- silkscreen/fabrication drawing primitives
- Altium stroke text rendered through `altium_monkey`
- simple Altium text expressions such as `.Comment`, `=Footprint`, and `=CurrentFootprint`
- component highlight bboxes from mechanical contour/assembly primitives, with pad extents as fallback

This is still a prototype: more Altium projects should be tested before treating the output as production-grade.
