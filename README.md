# Advanced Astronomy — Public Assignments

This repository contains **public releases** of Jupyter-based assignments from my Advanced Astronomy project. The goal of this project is to give beginning students in astronomy and astrophysics an opportunity to use professional-grade tools or something as close as possible to that. 

Assignments are released in a form suitable for student download and completion, and are organized into two courses suitable to a semester format:

- `aa_ss/` — *Advanced Astronomy: Solar System*
- `aa_u/` — *Advanced Astronomy: Universe*
- `aa_x/` — Shared material relevant to both courses

Each assignment appears in its own folder and includes:
- A Jupyter notebook (`.ipynb`) containing instructions, problems, and code cells.
- Any necessary images or supporting files.
- No solutions, test code, or instructor metadata.

---

## Usage

### For Students

Assignments are organized in subfolders inside this repository (*e.g.*, `aa_ss/ss_aristarchus/`). Each folder contains:

- A Jupyter Notebook (`.ipynb`)
- An `Images/` folder (if needed)
- Any supporting data or scripts

To work on an assignment:

1. Download or clone the **entire assignment folder** (*e.g.*, `ss_aristarchus/`) — not just the notebook file.
   > On ION, you can drag and drop the entire folder into your home directory using JupyterHub's file browser.
2. Open the notebook inside Jupyter (via JupyterHub or JupyterLab).
3. Run the cells and complete the work as directed in the notebook.

**Do not delete or move the `Images/` folder**, or any of the included files. Doing so may break image links and other resources in the notebook.

---

### Python Requirements

This repository includes a `requirements.txt` file specifying the minimal Python packages needed to run the assignments. These include:

- `numpy`, `math`, `astropy`
- `notebook`, `nbformat`
- `aa_tools`: the custom helper module used in many assignments it contains
   - `astroc` (deprecated), `astroconst`
   - `data/` directory containing data kernels (*e.g.* `de440.bsp`,`gm_de440.tpc`,`jup365.bsp`)

If you're working on a local machine or setting up a fresh environment (outside ION), you can install everything with:

```bash
pip install -r requirements.txt
```

---

## File Naming Convention

Each assignment is named using the format:

```
<course_prefix>_<topic>.ipynb
```

Where `<course_prefix>` is one of:
- `ss` = Solar System
- `u` = Universe
- `x` = Shared topics



