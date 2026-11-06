"""Plot actual fixed-test rates; optional reportlab/pypdfium2 dependencies."""

import argparse
import json
from pathlib import Path
import tempfile

import pypdfium2 as pdfium
from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib.colors import HexColor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    summary = json.loads((args.run / 'summary.json').read_text(encoding='utf-8'))
    prompt = summary['primary_prompt']; count = summary['fixed_test_examples']; groups = summary['independent_test_task_groups']
    drawing = Drawing(1000, 600)
    drawing.add(Rect(0, 0, 1000, 600, fillColor=HexColor('#ffffff'), strokeColor=None))
    drawing.add(String(65, 552, 'Base model vs best and final LoRA adapters', fontName='Helvetica-Bold', fontSize=24))
    drawing.add(String(65, 525, f'Fixed test: {count} records / {groups} task group(s) / identical {prompt} prompt and decoding', fontSize=13))
    metrics = [('json_valid', 'JSON parse valid', '#64748b'), ('schema_valid', 'Action schema valid', '#2563eb'),
               ('annotation_match', 'Annotation agreement', '#d97706')]
    for i, (_, label, color) in enumerate(metrics):
        drawing.add(Rect(80 + 290*i, 481, 18, 12, fillColor=HexColor(color), strokeColor=None))
        drawing.add(String(106 + 290*i, 481, label, fontSize=13))
    y0, height = 130, 300
    for fraction in (0, .25, .5, .75, 1):
        y = y0 + height*fraction
        drawing.add(Line(100, y, 940, y, strokeColor=HexColor('#e2e8f0'), strokeWidth=1))
        drawing.add(String(82, y - 4, f'{fraction:.0%}', fontSize=11, textAnchor='end'))
    for i, variant in enumerate(('base', 'best', 'final')):
        group = summary['comparison'][variant][prompt]['metrics']
        for j, (metric, _, color) in enumerate(metrics):
            value = group[metric]; x = 150 + i*275 + j*60; y = y0 + height*value['rate']
            drawing.add(Rect(x, y0, 45, max(.5, height*value['rate']), fillColor=HexColor(color), strokeColor=None))
            drawing.add(String(x + 22.5, y + 10, f'{value["correct"]}/{value["total"]}', fontSize=13, textAnchor='middle'))
        label = {'base': 'Base model', 'best': 'Best adapter (step 8)', 'final': 'Final adapter (step 12)'}[variant]
        drawing.add(String(230 + i*275, 98, label, fontSize=14, textAnchor='middle'))
    drawing.add(String(65, 53, 'JSON parsing alone does not establish action validity or correctness.', fontSize=13))
    drawing.add(String(65, 29, 'Annotation agreement requires ordered actions, non-coordinate parameters, and points within 10 original pixels.', fontSize=11))
    drawing.add(String(65, 10, 'No live GUI execution was performed. Two records from one task do not establish generalization.', fontSize=11))
    renderSVG.drawToFile(drawing, str(args.run / 'comparison-plot.svg'))
    with tempfile.TemporaryDirectory() as temporary:
        pdf = Path(temporary) / 'plot.pdf'; renderPDF.drawToFile(drawing, str(pdf))
        document = pdfium.PdfDocument(str(pdf)); page = document[0]; bitmap = page.render(scale=1.7)
        bitmap.to_pil().save(args.run / 'comparison-plot.png')
        bitmap.close(); page.close(); document.close()
    print(args.run / 'comparison-plot.png')


if __name__ == '__main__': main()
