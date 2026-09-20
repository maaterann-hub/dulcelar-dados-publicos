from __future__ import annotations

import csv
import json
import re
import shutil
import unicodedata
import zipfile

from datetime import datetime, timezone
from ftplib import FTP, error_perm
from pathlib import Path
from tempfile import TemporaryDirectory

import py7zr


FTP_HOST = "ftp.mtps.gov.br"
FTP_ROOT = "/pdet/microdados/NOVO CAGED"

CBO = "848310"
CBO_FORMATADO = "8483-10"
OCUPACAO = "Confeiteiro"

SAIDA = Path("dados/mte_confeiteiro.json")

FONTE_URL = (
    "https://www.gov.br/trabalho-e-emprego/pt-br/"
    "acesso-a-informacao/acoes-e-programas/"
    "programas-projetos-acoes-obras-e-atividades/"
    "estatisticas-trabalho/microdados-rais-e-caged"
)


def normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    texto = texto.lower().strip()

    return re.sub(
        r"[^a-z0-9]",
        "",
        texto,
    )


def somente_numeros(valor: str) -> str:
    return re.sub(
        r"\D",
        "",
        valor or "",
    )


def numero_decimal(valor: str) -> float:
    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = texto.replace("R$", "").strip()

    if "," in texto and "." in texto:
        texto = texto.replace(".", "")
        texto = texto.replace(",", ".")

    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return 0.0


def basename_ftp(valor: str) -> str:
    return valor.rstrip("/").split("/")[-1]


def listar(ftp: FTP, caminho: str) -> list[str]:
    anterior = ftp.pwd()

    try:
        ftp.cwd(caminho)
        itens = ftp.nlst()
    finally:
        ftp.cwd(anterior)

    return itens


def diretorio_existe(
    ftp: FTP,
    caminho: str,
) -> bool:
    anterior = ftp.pwd()

    try:
        ftp.cwd(caminho)
        ftp.cwd(anterior)

        return True

    except error_perm:
        try:
            ftp.cwd(anterior)
        except Exception:
            pass

        return False


def localizar_arquivo_mais_recente(
    ftp: FTP,
) -> tuple[str, str]:
    ano_atual = datetime.now().year

    anos = [
        ano_atual,
        ano_atual - 1,
    ]

    meses_encontrados = []

    for ano in anos:
        pasta_ano = f"{FTP_ROOT}/{ano}"

        if not diretorio_existe(
            ftp,
            pasta_ano,
        ):
            continue

        for item in listar(
            ftp,
            pasta_ano,
        ):
            nome = basename_ftp(item)

            if not re.fullmatch(
                rf"{ano}(0[1-9]|1[0-2])",
                nome,
            ):
                continue

            pasta_mes = (
                f"{pasta_ano}/{nome}"
            )

            if diretorio_existe(
                ftp,
                pasta_mes,
            ):
                meses_encontrados.append(
                    (
                        nome,
                        pasta_mes,
                    )
                )

    meses_encontrados.sort(
        reverse=True,
    )

    for competencia, pasta_mes in meses_encontrados:
        itens = listar(
            ftp,
            pasta_mes,
        )

        candidatos = []

        for item in itens:
            nome = basename_ftp(item)

            nome_maiusculo = nome.upper()

            if "MOV" not in nome_maiusculo:
                continue

            if nome_maiusculo.endswith(
                (
                    ".7Z",
                    ".ZIP",
                    ".TXT",
                )
            ):
                candidatos.append(nome)

        if candidatos:
            candidatos.sort()

            return (
                competencia,
                f"{pasta_mes}/{candidatos[0]}",
            )

    raise RuntimeError(
        "Nenhum arquivo de movimentações do Novo Caged foi encontrado."
    )


def baixar_arquivo(
    ftp: FTP,
    caminho_remoto: str,
    destino: Path,
) -> None:
    pasta = caminho_remoto.rsplit(
        "/",
        1,
    )[0]

    arquivo = caminho_remoto.rsplit(
        "/",
        1,
    )[1]

    anterior = ftp.pwd()

    try:
        ftp.cwd(pasta)

        with destino.open("wb") as saida:
            ftp.retrbinary(
                f"RETR {arquivo}",
                saida.write,
            )

    finally:
        ftp.cwd(anterior)


def extrair(
    arquivo: Path,
    pasta: Path,
) -> Path:
    extensao = arquivo.suffix.lower()

    if extensao == ".7z":
        with py7zr.SevenZipFile(
            arquivo,
            mode="r",
        ) as pacote:
            pacote.extractall(
                path=pasta,
            )

    elif extensao == ".zip":
        with zipfile.ZipFile(
            arquivo,
            mode="r",
        ) as pacote:
            pacote.extractall(
                path=pasta,
            )

    elif extensao == ".txt":
        destino = pasta / arquivo.name

        shutil.copy2(
            arquivo,
            destino,
        )

        return destino

    else:
        raise RuntimeError(
            f"Formato não suportado: {extensao}"
        )

    arquivos_txt = list(
        pasta.rglob("*.txt")
    )

    if not arquivos_txt:
        raise RuntimeError(
            "O pacote não contém arquivo TXT."
        )

    arquivos_mov = [
        arquivo_txt
        for arquivo_txt in arquivos_txt
        if "MOV" in arquivo_txt.name.upper()
    ]

    if arquivos_mov:
        return arquivos_mov[0]

    return arquivos_txt[0]


def localizar_coluna(
    nomes_normalizados: dict[str, str],
    alternativas: list[str],
) -> str:
    for alternativa in alternativas:
        chave = normalizar_texto(
            alternativa
        )

        if chave in nomes_normalizados:
            return nomes_normalizados[chave]

    raise RuntimeError(
        "Coluna necessária não encontrada: "
        + ", ".join(alternativas)
    )


def calcular_salario_medio(
    arquivo_txt: Path,
) -> tuple[float, int]:
    soma_salarios = 0.0
    quantidade = 0

    with arquivo_txt.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as arquivo:
        leitor = csv.DictReader(
            arquivo,
            delimiter=";",
        )

        if not leitor.fieldnames:
            raise RuntimeError(
                "O arquivo do Caged não possui cabeçalho."
            )

        campos = {
            normalizar_texto(nome): nome
            for nome in leitor.fieldnames
        }

        coluna_cbo = localizar_coluna(
            campos,
            [
                "cbo2002ocupação",
                "cbo2002ocupacao",
            ],
        )

        coluna_saldo = localizar_coluna(
            campos,
            [
                "saldomovimentação",
                "saldomovimentacao",
            ],
        )

        coluna_salario = localizar_coluna(
            campos,
            [
                "salário",
                "salario",
            ],
        )

        for linha in leitor:
            cbo_linha = somente_numeros(
                linha.get(
                    coluna_cbo,
                    "",
                )
            )

            if cbo_linha != CBO:
                continue

            saldo = numero_decimal(
                linha.get(
                    coluna_saldo,
                    "",
                )
            )

            # No Novo Caged, saldo +1 representa admissão.
            if saldo != 1:
                continue

            salario = numero_decimal(
                linha.get(
                    coluna_salario,
                    "",
                )
            )

            if salario <= 0:
                continue

            soma_salarios += salario
            quantidade += 1

    if quantidade == 0:
        raise RuntimeError(
            "Nenhuma admissão válida foi encontrada para "
            f"o CBO {CBO_FORMATADO}."
        )

    media = soma_salarios / quantidade

    return media, quantidade


def competencia_formatada(
    competencia: str,
) -> str:
    if len(competencia) != 6:
        return competencia

    return (
        f"{competencia[:4]}-"
        f"{competencia[4:]}"
    )


def salvar_json(
    media: float,
    quantidade: int,
    competencia: str,
) -> None:
    SAIDA.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dados = {
        "schema_version": 1,
        "cbo": CBO_FORMATADO,
        "ocupacao": OCUPACAO,
        "metrica": "salario_medio_admissao",
        "abrangencia": "Brasil",
        "competencia": competencia_formatada(
            competencia
        ),
        "salario_medio_admissao_centavos": round(
            media * 100
        ),
        "admissoes_consideradas": quantidade,
        "fonte": (
            "Novo Caged / Ministério do Trabalho e Emprego"
        ),
        "fonte_url": FONTE_URL,
        "metodologia": (
            "Média dos salários mensais declarados nas admissões "
            "do CBO 8483-10 na competência mais recente disponível."
        ),
        "gerado_em": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    SAIDA.write_text(
        json.dumps(
            dados,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    print(
        "Conectando ao FTP oficial do MTE..."
    )

    ftp = FTP(
        FTP_HOST,
        timeout=60,
    )

    ftp.login()

    try:
        competencia, remoto = (
            localizar_arquivo_mais_recente(
                ftp
            )
        )

        print(
            f"Competência encontrada: {competencia}"
        )

        print(
            f"Arquivo encontrado: {remoto}"
        )

        with TemporaryDirectory() as temporario:
            pasta = Path(
                temporario
            )

            pacote = pasta / basename_ftp(
                remoto
            )

            baixar_arquivo(
                ftp,
                remoto,
                pacote,
            )

            print(
                "Download concluído."
            )

            pasta_extraida = (
                pasta / "extraido"
            )

            pasta_extraida.mkdir()

            arquivo_txt = extrair(
                pacote,
                pasta_extraida,
            )

            print(
                f"Processando: {arquivo_txt.name}"
            )

            media, quantidade = (
                calcular_salario_medio(
                    arquivo_txt
                )
            )

            salvar_json(
                media,
                quantidade,
                competencia,
            )

            print(
                "Referência calculada."
            )

            print(
                f"Admissões consideradas: {quantidade}"
            )

            print(
                f"Salário médio: R$ {media:.2f}"
            )

            print(
                f"Arquivo criado: {SAIDA}"
            )

    finally:
        ftp.quit()


if __name__ == "__main__":
    main()
