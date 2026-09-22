"""Script de compilação nativa com Cython dos módulos proprietários NexoJuris.

Gera extensões binárias (.pyd no Windows) a partir do código Python puro,
dificultando a leitura de código-fonte, descompilação e engenharia reversa.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from setuptools import Extension, setup

PROJECT_ROOT = Path(__file__).resolve().parent

# Lista de módulos proprietários a serem compilados em C/pyd
MODULES = [
    ("license_key_config", "license_key_config.py"),
    ("trusted_time", "trusted_time.py"),
    ("models", "models.py"),
    ("license_core.protocol", "license_core/protocol.py"),
    ("license_core.state", "license_core/state.py"),
    ("licensing", "licensing.py"),
    ("library_db", "library_db.py"),
    ("converter", "converter.py"),
    ("web_api", "web_api.py"),
]


def get_extensions() -> list[Extension]:
    extensions = []
    for mod_name, file_rel in MODULES:
        file_path = PROJECT_ROOT / file_rel
        if not file_path.is_file():
            print(f"[AVISO] Arquivo não encontrado: {file_path}")
            continue
        extensions.append(
            Extension(
                name=mod_name,
                sources=[str(file_path)],
                extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O2"],
            )
        )
    return extensions


def clean_c_files() -> None:
    """Remove arquivos intermediários .c gerados pelo Cython."""
    for _, file_rel in MODULES:
        c_file = PROJECT_ROOT / (os.path.splitext(file_rel)[0] + ".c")
        if c_file.is_file():
            try:
                c_file.unlink()
                print(f"Removido: {c_file.name}")
            except OSError:
                pass


def build_extensions(inplace: bool = True) -> None:
    from Cython.Build import cythonize

    extensions = get_extensions()
    if not extensions:
        print("Nenhum módulo encontrado para compilar.")
        return

    print(f"=== Compilando {len(extensions)} módulos proprietários com Cython ===")
    ext_modules = cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "always_allow_keywords": True,
            "binding": True,
            "annotation_typing": False,
        },
        quiet=False,
    )

    args = ["build_ext"]
    if inplace:
        args.append("--inplace")

    setup(
        name="NexoJurisNative",
        packages=[],
        py_modules=[],
        ext_modules=ext_modules,
        script_args=args,
    )
    print("=== Compilação nativa concluída com sucesso ===")


if __name__ == "__main__":
    clean = "--clean" in sys.argv
    inplace = "--build-dir" not in sys.argv

    if clean:
        clean_c_files()
    else:
        try:
            build_extensions(inplace=inplace)
        finally:
            clean_c_files()
