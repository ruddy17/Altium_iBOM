# Altium iBOM

Experimental Altium Designer to InteractiveHtmlBom converter.

This project reads native Altium PCB data with
[`altium_monkey`](https://github.com/wavenumber-eng/altium_monkey), converts it
to the InteractiveHtmlBom generic JSON format, and can optionally call
[`interactivehtmlbom`](https://github.com/openscopeproject/interactivehtmlbom)
to generate the final standalone HTML BOM.

The current goal is a practical Altium edition of InteractiveHtmlBom: good board
preview fidelity, correct component-to-BOM selection, and eventually a simple
Altium GUI command that runs the converter from inside Altium Designer.

## Repository Contents

```text
Altium_iBOM/
  altium_to_ibom.py   Main converter CLI
  altium-plugin/      One-button Altium DelphiScript integration
  requirements.txt    Python dependencies
  README.md           Project notes and handoff
```

Useful adjacent development clones:

```text
parent/
  Altium_iBOM/
  altium_monkey/
  interactivehtmlbom/
```

The converter looks for local adjacent clones first. You can also set explicit
paths with environment variables.

## Setup

From inside this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If developing against local source checkouts instead of installed packages:

```powershell
$env:ALTIUM_MONKEY_PATH="C:\path\to\altium_monkey\src\py"
$env:INTERACTIVEHTMLBOM_PATH="C:\path\to\interactivehtmlbom"
```

## Usage

Generate InteractiveHtmlBom generic JSON:

```powershell
.\.venv\Scripts\python.exe .\altium_to_ibom.py "C:\path\to\Board.PrjPcb" -o .\board.ibom-input.json
```

Generate JSON and standalone HTML:

```powershell
.\.venv\Scripts\python.exe .\altium_to_ibom.py "C:\path\to\Board.PrjPcb" -o .\board.ibom-input.json --html --html-name board.ibom
```

Demo project used during development:

```text
C:\exo\PewCB.Demo.SensorsTH.Electronics
```

Example:

```powershell
.\.venv\Scripts\python.exe .\altium_to_ibom.py "C:\exo\PewCB.Demo.SensorsTH.Electronics\PewCB.Demo.SensorsTH.PrjPcb" -o .\demo.ibom-input.json --html --html-name demo.ibom
```

## Input Files

The preferred input is an Altium `*.PrjPcb` project. Through the project,
`altium_monkey` loads the referenced `*.PcbDoc` and schematic/project metadata.
The converter also accepts a standalone `*.PcbDoc`; its board geometry and PCB
components are available, but schematic-only BOM fields are naturally absent.

The PCB document contains the geometry needed for the board view:

- components and placements
- pads, vias, free through-hole pads, and holes
- copper tracks, arcs, fills, regions, and zones
- board outline segments and arc corners
- silkscreen, mechanical, and assembly primitives
- PCB text and component text

## Altium One-Button Integration

The `altium-plugin` directory contains a DelphiScript Global Project. It detects
the focused PCB project or document, runs the Python converter, writes the
result to an `ibom` directory beside the source, and opens the generated HTML.

Run the installer once from PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\altium-plugin\install.ps1
```

The installer creates the Altium-side launcher inside the visible repository:

```text
<repository>\altium-plugin\installed\Altium_iBOM.PrjScr
```

It records the current converter and Python paths in a local `config.cmd`. If
the repository or virtual environment is moved, rerun the installer.

In Altium Designer:

1. Open **Preferences > Scripting System > Global Projects**.
2. Add `altium-plugin\installed\Altium_iBOM.PrjScr` from this repository.
3. Use **File > Run Script** once and run
   `Altium_iBOM.pas > GenerateInteractiveBom` to verify it.
4. Open the PCB editor's **Customize** dialog, create a command using process
   `ScriptingSystem:RunScript`, and use the script parameters copied from the
   Run Script dialog.
5. Drag that command onto a PCB toolbar.

The command prefers the focused `*.PrjPcb` because that supplies schematic BOM
metadata. If there is no PCB project, it falls back to the active `*.PcbDoc`.
No Altium project needs to be created solely for iBOM generation.

## Output Files

The converter writes:

- `*.ibom-input.json`: InteractiveHtmlBom generic JSON input
- optional `*.html`: final InteractiveHtmlBom standalone file

Generated JSON/HTML files are intentionally ignored by git.

## Conversion Pipeline

At a high level:

1. Load the Altium design with `altium_monkey`.
2. Resolve the PCB layer stack.
3. Build BOM component rows and footprint entries in matching index order.
4. Convert pads and footprint drawings.
5. Convert board tracks, arcs, vias, zones, drawings, and outline edges.
6. Emit InteractiveHtmlBom generic JSON.
7. Optionally invoke `InteractiveHtmlBom.generate_interactive_bom`.

Index order matters: InteractiveHtmlBom links board clicks to BOM rows by
matching `components[i]` with `pcbdata.footprints[i]`. Do not sort one list
without applying the same ordering to the other.

## Current Coverage

Implemented and tested on the demo board:

- BOM component metadata and extra fields
- component pads, including pin-1 markers
- SMD rectangular, rounded-rectangle, oval, circular, and custom-ish pad shapes
- free pads and vias rendered through a hidden virtual footprint, without BOM
  rows or click-selection interference
- copper tracks, arcs, vias, and zones
- board outline segments and rounded arc corners
- full-circle drawing arcs emitted as circles
- silkscreen, fabrication, mechanical, and assembly drawing primitives
- top designator helper text
- Altium stroke text converted through `altium_monkey`
- simple text expressions such as `.Comment`, `=Footprint`, and
  `=CurrentFootprint`
- component highlight boxes from mechanical contour/assembly primitives, with
  pad extents as fallback

## Important Fixes Already Made

These were real visual/click bugs found during development:

- Arc directions were inverted because Altium and iBOM use different coordinate
  conventions.
- Full-circle arcs collapsed to a nearly invisible zero-degree arc.
- Board outline arc corners were initially emitted as straight chords.
- Many rounded-rectangle pads were incorrectly rendered as ovals.
- Oval pads on the STM32 footprint were incorrectly rendered as rectangles.
- Free through-hole pads around the board edge were missing.
- Component-to-BOM click correspondence broke when components were sorted but
  footprints were not.
- `.Comment` text macros needed recursive resolution through footprint/current
  footprint fields.
- Regular hidden overlay designators/comments should stay hidden, but the top
  designator helper layer should remain visible.
- Component highlight boxes should not be based only on pad centers.

## Current Altium UX

The first integration deliberately has no settings dialog. One command produces
JSON and HTML in `<design directory>\ibom` and opens the HTML. Conversion errors
show a message with the log path. A native DelphiScript settings form can be
added later without changing the Python engine.

## Handoff Notes For Future Agents

Start by reading `altium_to_ibom.py`. The important orchestration function is
`build_payload()`. Most geometry conversion helpers live above it.

When touching visual output, generate a fresh JSON/HTML from the demo project
and inspect it in InteractiveHtmlBom. Many bugs only show up visually.

When touching component ordering, verify that every `components[i]["ref"]`
matches `pcbdata["footprints"][i]["ref"]`.

When touching pads, test both passive two-pad parts and the STM32 footprint:
they exercise rounded rectangles and true oval pads differently.

When touching text, check both regular component overlay text and helper-layer
top designators. They intentionally have different visibility behavior.

Standalone `*.PcbDoc` input uses `AltiumDesign.from_pcbdoc(path)`; project input
uses `AltiumDesign.from_prjpcb(path)`.

This is still prototype-grade. The demo board is a good regression target, but
more Altium projects are needed before claiming broad compatibility.
