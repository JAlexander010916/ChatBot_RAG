"""
Prueba de humo del procesador de documentos.

No requiere pytest: se ejecuta directamente con

    python backend/tests/test_document_processor.py

Su objetivo es detectar errores estructurales como un metodo renombrado o borrado
(por ejemplo `def _` en lugar de `def _process_pdf`), que solo se manifiestan
en tiempo de ejecucion cuando ya se subio un documento.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services.document_processor import DocumentProcessor  # noqa: E402


fallos = []


def comprobar(condicion, descripcion):
    if condicion:
        print(f"  OK   {descripcion}")
    else:
        print(f"  FALLO {descripcion}")
        fallos.append(descripcion)


def test_existen_los_extractores():
    """Todos los métodos despachados por process_document deben existir."""
    print("\n[1] Existen los extractores despachados por process_document")
    for metodo in ("_process_pdf", "_process_docx", "_process_doc"):
        comprobar(
            callable(getattr(DocumentProcessor, metodo, None)),
            f"DocumentProcessor.{metodo} existe y es invocable",
        )


def test_no_hay_metodos_con_nombre_truncado():
    """Detecta identificadores tipo `def _` generados por una edición accidental."""
    print("\n[2] No hay nombres de método truncados")
    metodos = [n for n in dir(DocumentProcessor) if not n.startswith("__")]
    sospechoso = [n for n in metodos if n.strip("_") == ""]
    comprobar(not sospechoso, f"sin métodos sin nombre (encontrados: {sospechoso})")


def test_chunking_y_limpieza():
    """El chunking debe conservar el número de página en documentos paginados."""
    print("\n[3] Chunking y limpieza")
    chunks = DocumentProcessor._create_chunks("palabra " * 300, page=7)
    comprobar(len(chunks) > 0, "_create_chunks produce chunks")
    comprobar(
        all(c.get("page") == 7 for c in chunks),
        "los chunks conservan la página de origen",
    )
    limpio = DocumentProcessor._clean_text("a  \r\n\r\n  b\x00c", preserve_paragraphs=True)
    comprobar("\x00" not in limpio, "_clean_text elimina caracteres nulos")


def test_pdf_real_en_repo():
    """Procesa un PDF real si existe en el repositorio."""
    print("\n[4] Procesado de un PDF real")
    pdfs = list(Path(__file__).resolve().parent.parent.glob("documentos/**/*.pdf"))
    if not pdfs:
        print("  OMITIDO no hay PDFs en backend/documentos")
        return
    resultado = None
    try:
        resultado = DocumentProcessor.process_document(str(pdfs[0]), pdfs[0].name)
    except Exception as exc:
        comprobar(False, f"process_document lanzo {type(exc).__name__}: {exc}")
    if resultado is not None:
        comprobar(resultado["pages"] > 0, f"se detectaron paginas ({resultado['pages']})")
        comprobar(len(resultado["chunks"]) > 0, f"se generaron chunks ({len(resultado['chunks'])})")
        comprobar(len(resultado["text"].strip()) > 0, "se extrajo texto")


def test_pdf_y_docx_generados():
    """Procesa un PDF y un DOCX creados al vuelo, sin depender del repo."""
    print("\n[5] Procesado de PDF y DOCX generados en el momento")

    try:
        from docx import Document as DocxDocument
    except ImportError:
        print("  OMITIDO python-docx no disponible")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "prueba.docx"
            doc = DocxDocument()
            doc.add_paragraph("Primer parrafo de la prueba.")
            doc.add_paragraph("Segundo parrafo con mas palabras " * 30)
            doc.save(ruta)
            try:
                resultado = DocumentProcessor.process_document(str(ruta), ruta.name)
                comprobar(
                    "Primer parrafo" in resultado["text"],
                    "DOCX generado: texto extraido correctamente",
                )
            except Exception as exc:
                comprobar(
                    False,
                    f"DOCX generado: proceso fallo ({type(exc).__name__}: {exc})",
                )

    try:
        import PyPDF2
    except ImportError:
        print("  OMITIDO PyPDF2 no disponible")
        return

    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "prueba.pdf"
        escritor = PyPDF2.PdfWriter()
        escritor.add_blank_page(width=200, height=200)
        with open(ruta, "wb") as archivo:
            escritor.write(archivo)
        del escritor
        # Un PDF sin texto no debe lanzar AttributeError: lo que interesa
        # es que el despachador encuentre _process_pdf.
        resultado = None
        try:
            resultado = DocumentProcessor.process_document(str(ruta), ruta.name)
            comprobar(resultado["pages"] == 1, "PDF generado: se detecto 1 pagina")
        except Exception as exc:
            comprobar(
                "AttributeError" not in str(exc),
                f"PDF generado: sin AttributeError ({type(exc).__name__}: {exc})",
            )


if __name__ == "__main__":
    test_existen_los_extractores()
    test_no_hay_metodos_con_nombre_truncado()
    test_chunking_y_limpieza()
    test_pdf_real_en_repo()
    test_pdf_y_docx_generados()

    print("\n" + "=" * 60)
    if fallos:
        print(f"RESULTADO: {len(fallos)} comprobacion(es) fallida(s)")
        for fallo in fallos:
            print(f"  - {fallo}")
        sys.exit(1)
    print("RESULTADO: todas las comprobaciones pasaron")
