import pandas as pd
from unidecode import unidecode
from validate_docbr import CPF
import re
import requests
import json
import base64
import pyodbc
import numpy as np
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from pathlib import Path

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

def tratar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Função que trata um DataFrame conforme as regras especificadas,
    incluindo a validação de CPFs duplicados e CEPs inválidos.
    
    Args:
        df (pd.DataFrame): O DataFrame a ser tratado.
        
    Returns:
        pd.DataFrame: O DataFrame tratado com as colunas CADASTRADO e MOTIVO.
    """
    df_copia = df.copy()

    # 1. Padronizar para maiúsculas (exceto EMAIL), remover quebras de linha e espaços extras
    for col in df_copia.columns:
        if df_copia[col].dtype == 'object':
            # Limpeza comum para todos
            df_copia[col] = df_copia[col].astype(str).str.strip().str.replace(r'[\r\n]+', '', regex=True)
            
            if col != 'EMAIL':
                # TUDO PARA QUEM NÃO É EMAIL
                df_copia[col] = df_copia[col].str.upper() # Maiúsculo
                df_copia[col] = df_copia[col].str.replace(r'[.,;]', '', regex=True) # Remove pontos e vírgulas
                
            else:
                # TUDO PARA A COLUNA EMAIL
                df_copia[col] = df_copia[col].str.lower() # Minúsculo
                df_copia[col] = df_copia[col].str.replace(r'[,;]', '', regex=True) # Remove SÓ a vírgula

    # 2. Remover acentuação e caracteres especiais das colunas de texto
    cols_para_limpar = ['NOME', 'RUA', 'COMPLEMENTO', 'BAIRRO', 'CIDADE', 'UF']
    for col in cols_para_limpar:
        if col in df_copia.columns:
            df_copia[col] = df_copia[col].astype(str)
            df_copia[col] = df_copia[col].apply(lambda x: unidecode(str(x)) if pd.notna(x) else x)
            # Adiciona a limpeza de caracteres especiais para a coluna 'RUA'
            if col in ['RUA', 'COMPLEMENTO']:
                df_copia[col] = df_copia[col].str.replace(r'[^A-Z0-9\s/]+', '', regex=True)
            else:
                df_copia[col] = df_copia[col].str.replace(r'[^A-Z0-9\s]+', '', regex=True)

    # ... (o restante da função continua inalterado) ...
    #
    # 3. Tratamento específico para a coluna 'CPF' e validação
    if 'CPF' in df_copia.columns:
        df_copia['CPF'] = df_copia['CPF'].astype(str)
        def formatar_e_validar_cpf(cpf_str):
            if pd.isna(cpf_str) or cpf_str.lower() in ('nan', ''):
                return None, False
            cpf_numeros = re.sub(r'[^0-9]', '', str(cpf_str))
            
            validador_cpf = CPF()
            is_valido = validador_cpf.validate(cpf_numeros) and len(cpf_numeros) == 11
            
            return cpf_numeros, is_valido

        df_copia[['CPF', 'CPF_VALIDO']] = df_copia['CPF'].apply(lambda x: pd.Series(formatar_e_validar_cpf(x)))
    else:
        df_copia['CPF'] = None
        df_copia['CPF_VALIDO'] = False

    # 4. Tratamento específico para a coluna 'NUMERO' e 'COMPLEMENTO'
    if 'NUMERO' in df_copia.columns and 'COMPLEMENTO' in df_copia.columns:
        # Preenche NaN com string vazia para facilitar o tratamento
        df_copia['NUMERO'] = df_copia['NUMERO'].fillna('').astype(str)
        df_copia['COMPLEMENTO'] = df_copia['COMPLEMENTO'].fillna('').astype(str)
        
        def tratar_numero_e_complemento(row):
            numero_str = str(row['NUMERO']).upper().strip()
            complemento_str = str(row['COMPLEMENTO']).upper().strip()
            
            # Remove "Nº" e o "S/N" e substitui por "N SN" para evitar quebra do texto
            numero_str = re.sub(r'N[º]', 'N ', numero_str)
            numero_str = numero_str.replace('S/N', 'SN')
            
            # Caso 1: Se o número for vazio, nulo, 'SN' ou 'S/N' (ou variantes)
            match_sn = re.search(r'\bS\s?N\b|\bSEM\s?N[UÚ]MERO\b', numero_str)
            if numero_str in ['NAN', ''] or pd.isna(numero_str) or numero_str in ['-', '0'] or match_sn:
                if re.search(r'\bS\s?N\b|\bSEM\s?N[UÚ]MERO\b', numero_str) or numero_str in ['NAN', '']:
                    return 'SN', complemento_str
                if numero_str in ['-', '0']:
                    return 'SN', complemento_str
            
            # Caso 2: Se o número contiver a palavra 'CASA', 'LOTE', 'APTO', etc.
            match_complexo = re.search(r'([A-Z]+)\s*(\d+)', numero_str)
            if match_complexo:
                texto, numero = match_complexo.groups()
                if numero:
                    novo_complemento = f'{complemento_str} {texto}'
                    return numero.strip(), novo_complemento.strip()

            # Caso 3: Tenta extrair um número no início da string
            match_numero_padrao = re.search(r'^(\d+)', numero_str)
            if match_numero_padrao:
                numero = match_numero_padrao.group(1)
                resto_numero = re.sub(r'^\d+', '', numero_str).strip()
                novo_complemento = f'{complemento_str} {resto_numero}'.strip() if resto_numero else complemento_str
                return numero, novo_complemento

            # Caso 4: Se nada funcionar, move o conteúdo inteiro de NUMERO para COMPLEMENTO e deixa NUMERO como 'SN'
            else:
                novo_complemento = f'{complemento_str} {numero_str}'.strip()
                return 'SN', novo_complemento

        df_copia[['NUMERO', 'COMPLEMENTO']] = df_copia.apply(tratar_numero_e_complemento, axis=1, result_type='expand')

    # 5. Tratamento para 'UF'
    if 'UF' in df_copia.columns:
        df_copia['UF'] = df_copia['UF'].astype(str).str.slice(0, 2)
    
    # 6. Validação de duplicidade e criação das colunas CADASTRADO e MOTIVO
    df_copia['CADASTRADO'] = 'SIM'
    df_copia['MOTIVO'] = ''

    if 'CPF' in df_copia.columns:
        cpfs_duplicados = df_copia.loc[df_copia['CPF'].duplicated(keep='first'), 'CPF'].unique()
        df_copia.loc[df_copia['CPF'].isin(cpfs_duplicados) & df_copia['CPF'].duplicated(keep='first'), 'CADASTRADO'] = 'NAO'
        df_copia.loc[df_copia['CPF'].isin(cpfs_duplicados) & df_copia['CPF'].duplicated(keep='first'), 'MOTIVO'] = 'ENVIADO EM DUPLICIDADE NA BASE'

    # 7. Tratamento e validação de CEP
    if 'CEP' in df_copia.columns:
        df_copia['CEP'] = df_copia['CEP'].astype(str)
        def tratar_cep(cep_str):
            if pd.isna(cep_str) or cep_str.lower() in ('nan', ''):
                return None, False
            cep_limpo = re.sub(r'[^0-9]', '', str(cep_str))
            return cep_limpo, len(cep_limpo) == 8

        df_copia[['CEP', 'CEP_VALIDO']] = df_copia['CEP'].apply(lambda x: pd.Series(tratar_cep(x)))
        df_copia.loc[df_copia['CEP'] == False, 'CADASTRADO'] = 'NAO'
        df_copia.loc[df_copia['CEP'] == False, 'MOTIVO'] = 'CEP INVALIDO'
    
    # 8. Tratamento de valores 'NaN' ou vazios para '-' no final do processo
    if 'COMPLEMENTO' in df_copia.columns:
        df_copia['COMPLEMENTO'] = df_copia['COMPLEMENTO'].replace('', '-').replace('NAN', '-').fillna('-')
    if 'NUMERO' in df_copia.columns:
        df_copia['NUMERO'] = df_copia['NUMERO'].replace('', '-').replace('NAN', '-').fillna('-')
    
    # 9. Limpeza final para a coluna 'RUA'
    if 'RUA' in df_copia.columns:
        df_copia['RUA'] = df_copia['RUA'].astype(str).str.replace(r'\bNO\s?SN\b', 'N SN', regex=True)
        df_copia['RUA'] = df_copia['RUA'].str.replace(r'\bN[º°]\s*S/?N\b', 'N SN', regex=True)
        df_copia['RUA'] = df_copia['RUA'].str.replace(r'S/N', 'SN', regex=True)
    

    return df_copia

# --- Nova função para tratar as colunas DDD e TELEFONE ---
def tratar_telefone_e_ddd(df: pd.DataFrame) -> pd.DataFrame:
    """
    Trata as colunas DDD e TELEFONE.
    - DDD: Remove caracteres não numéricos e formata para 4 dígitos com zeros à esquerda.
    - TELEFONE: Remove caracteres não numéricos.
    """
    df_copia = df.copy()
    if 'DDD' in df_copia.columns:
        df_copia['DDD'] = df_copia['DDD'].astype(str).str.replace(r'[^0-9]', '', regex=True)
        df_copia['DDD'] = df_copia['DDD'].apply(lambda x: f'{int(x):04d}' if len(x) == 2 else x)

    if 'TELEFONE' in df_copia.columns:
        df_copia['TELEFONE'] = df_copia['TELEFONE'].astype(str).str.replace(r'[^0-9]', '', regex=True)

    return df_copia


# --- Função para autenticar na API dos Correios ---
def autenticar_correios():
    """
    Autentica na API dos Correios e retorna o token de acesso.
    """
    usuario = os.getenv("CORREIOS_USUARIO")
    senha = os.getenv("CORREIOS_SENHA")
    numero_contrato = os.getenv("CORREIOS_CONTRATO")

    # Valida se as variáveis de ambiente existem
    if not all([usuario, senha, numero_contrato]):
        print("Erro: Variáveis de ambiente dos Correios não configuradas corretamente (USER, PASS ou CONTRATO).")
        return None

    try:
        # Codifica as credenciais em Base64 de forma dinâmica
        credenciais = f"{usuario}:{senha}"
        credenciais_base64 = base64.b64encode(credenciais.encode()).decode()

        url = "https://api.correios.com.br/token/v1/autentica/contrato"
        payload = json.dumps({"numero": numero_contrato})

        headers = {
            'accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Basic {credenciais_base64}'
        }
        
        response = requests.post(url, headers=headers, data=payload)
        response.raise_for_status()
        
        return response.json().get("token")
        
    except requests.exceptions.HTTPError as err:
        print(f"Erro na autenticação da API dos Correios: {err} - Resposta: {err.response.text}")
        return None
    except Exception as e:
        print(f"Erro inesperado na autenticação: {e}")
        return None




def consulta_endereco_por_cep(cep, token):
    """
    Consulta a API dos Correios para obter dados de endereço.
    """
    time.sleep(0.05)
    if not token or not cep:
        return {'logradouro': None, 'bairro': None, 'localidade': None, 'uf': None}

    url = f"https://api.correios.com.br/cep/v2/enderecos/{cep}"
    
    headers = {
        'accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return {
            'logradouro': data.get('logradouro', None),
            'bairro': data.get('bairro', None),
            'localidade': data.get('localidade', None),
            'uf': data.get('uf', None)
        }
    except requests.exceptions.HTTPError as err:
        print(f"Erro ao consultar CEP {cep}: {err}")
        return {'logradouro': None, 'bairro': None, 'localidade': None, 'uf': None}
    except Exception as e:
        print(f"Erro inesperado ao consultar CEP {cep}: {e}")
        return {'logradouro': None, 'bairro': None, 'localidade': None, 'uf': None}






# --- Função para adicionar UF da API e comparar ---
def adicionar_uf_api(df: pd.DataFrame, caminho_salvar_api: str = None) -> pd.DataFrame:
    """
    Consulta a API dos Correios em paralelo via Threads, gera um DataFrame com os dados completos,
    salva este DataFrame à parte em um arquivo Excel/CSV e valida a coluna UF original com a UF_API.
    """
    token_novo = autenticar_correios()
    if not token_novo:
        print("Não foi possível obter o token da API. As colunas UF_API e UF_CONFERE não serão criadas.")
        df['UF_API'] = None
        df['UF_CONFERE'] = 'ERRO NA VERIFICACAO'
        return df

    lista_ceps = df['CEP'].astype(str).tolist()
    total = len(lista_ceps)
    resultados_map = {}

    print(f"\nIniciando consulta paralela da API dos Correios para {total} CEPs...")

    # 1. Execução Concorrente com ThreadPoolExecutor
    def executar_processamento(ceps, token):
        with ThreadPoolExecutor(max_workers=3) as executor:
            tarefas = {executor.submit(consulta_endereco_por_cep, cep, token): i for i, cep in enumerate(ceps)}
            contagem = 0
            for futura in as_completed(tarefas):
                index = tarefas[futura]
                contagem += 1
                try:
                    dados = futura.result()
                    if not isinstance(dados, dict):
                        dados = {}
                    
                    # CORREÇÃO AQUI: Considera sucesso se retornou UF ou Localidade
                    status = "Sucesso" if dados.get('uf') or dados.get('localidade') else "Falha"
                    
                    print(f"[{contagem}/{total}] CEP: {ceps[index]} - Status: {status}")
                    
                    # Garante a presença do CEP pesquisado no dicionário de retorno
                    dados['cep_pesquisado'] = ceps[index]
                    resultados_map[index] = dados
                except Exception as e:
                    print(f"[{contagem}/{total}] Erro no CEP {ceps[index]}: {e}")
                    resultados_map[index] = {
                        'cep_pesquisado': ceps[index],
                        'logradouro': None, 
                        'bairro': None, 
                        'localidade': None, 
                        'uf': None, 
                        'abreviatura': None
                    }
        return [resultados_map[i] for i in range(total)]

    resultados_lista = executar_processamento(lista_ceps, token_novo)

    # 2. Construção e Tratamento do DataFrame da API
    df_api = pd.DataFrame(resultados_lista)

    colunas_texto = ['logradouro', 'bairro', 'localidade', 'uf', 'abreviatura']
    for col in colunas_texto:
        if col not in df_api.columns:
            df_api[col] = None

    # Dicionário de substituição de caracteres especiais / acentos
    mapa_substituicao = {
        r'[àáâãäÀÁÂÃÄ]': 'A', r'[èéêëÈÉÊË]': 'E', r'[ìíîïÌÍÎÏ]': 'I',
        r'[òóôõöÒÓÔÕÖ]': 'O', r'[ùúûüÙÚÛÜ]': 'U', r'[çÇ]': 'C', r'[ñÑ]': 'N',
        r'[,&°º/?!*\.\-:\;\']': ' ', r'\s+': ' '
    }

    # Aplica a limpeza vetorizada em todas as colunas da API
    for col in colunas_texto:
        df_api[col] = df_api[col].fillna('').astype(str)
        for padrao, sub in mapa_substituicao.items():
            df_api[col] = df_api[col].str.replace(padrao, sub, regex=True)
        df_api[col] = df_api[col].str.strip().str.upper()

    # 3. Salvar o DataFrame da API separadamente
    if not caminho_salvar_api:
        caminho_salvar_api = BASE_DIR / "data" / "base_enderecos_api_correios.xlsx"
        
    try:
        df_api.to_excel(caminho_salvar_api, index=False)
        print(f"\nDataFrame da API salvo com sucesso em: '{caminho_salvar_api}'")
    except Exception as e:
        print(f"\nErro ao salvar o DataFrame da API separadamente: {e}")

    # 4. Conciliação das UFs com o DataFrame Principal
    df['UF_API'] = df_api['uf'].values
    df['UF'] = df['UF'].astype(str).str.strip().str.upper()

    df['UF_CONFERE'] = np.where(df['UF'] == df['UF_API'], 'SIM', 'NAO')

    print("Conferência de UF finalizada.")
    return df


# --- Nova função para conciliar com a base do IBGE ---
def conciliar_com_ibge(df: pd.DataFrame, caminho_ibge: str) -> pd.DataFrame:
    """
    Faz a conciliação do DataFrame com a base do IBGE para adicionar
    informações de Aliquota, Região e Código.
    """
    print(f"\nIniciando a conciliação com a base do IBGE no caminho: {caminho_ibge}...")
    try:
        df_ibge = pd.read_excel(caminho_ibge)
        
        # Padroniza as colunas de texto para maiúsculas
        df_ibge = df_ibge.applymap(lambda x: str(x).upper() if pd.notnull(x) and isinstance(x, str) else x)
        
        # Cria a coluna de concatenação em ambos os DataFrames
        df_tratado['CONCATENAR'] = df_tratado['CIDADE'] + ' - ' + df_tratado['UF']
        df['CONCATENAR'] = df['CIDADE'].fillna('') + ' - ' + df['UF'].fillna('')
        
        # Realiza o merge para trazer as novas colunas
        df_conciliado = pd.merge(df_tratado, df_ibge[['Aliquota', 'Região', 'Código', 'CONCATENAR']], 
                                 on='CONCATENAR', 
                                 how='left')
        # Atualiza as colunas no DataFrame original
        df['Aliquota'] = df_conciliado['Aliquota']
        df['Região'] = df_conciliado['Região']
        df['Código'] = df_conciliado['Código']
        
        # Remove a coluna temporária 'CONCATENAR'
        #df.drop('CONCATENAR', axis=1, inplace=True)
        
        print("Conciliação com a base do IBGE concluída. Colunas adicionadas: Aliquota, Região, Código.")
    except FileNotFoundError:
        print(f"Erro: O arquivo do IBGE não foi encontrado no caminho '{caminho_ibge}'. As colunas não serão adicionadas.")
    except Exception as e:
        print(f"Ocorreu um erro na conciliação com o IBGE: {e}")
        
    return df

# --- Função adicionada para alertar sobre valores longos ---
def alertar_colunas_longas(df: pd.DataFrame, limite: int = 40) -> pd.DataFrame:
    """
    Verifica se alguma string nas colunas do DataFrame excede o limite de caracteres
    e adiciona uma nova coluna 'ALERTA' para indicar os casos, incluindo a contagem exata.
    Retorna o DataFrame modificado.
    """
    df['ALERTA'] = ''
    colunas_verificadas = ['RUA', 'COMPLEMENTO', 'BAIRRO', 'CIDADE', 'NOME']

    print("\n--- Verificando valores longos e criando coluna de alerta ---")
    
    for col in colunas_verificadas:
        if col in df.columns:
            # CORREÇÃO AQUI: Converte a coluna para str antes de verificar o tamanho
            df[col] = df[col].astype(str)
            indices_longos = df[df[col].str.len() > limite].index
            
            for index in indices_longos:
                tamanho_valor = len(df.loc[index, col])
                mensagem_alerta = f'[{col} COM {tamanho_valor} CARACTERES]'
                
                if df.loc[index, 'ALERTA']:
                    df.loc[index, 'ALERTA'] += f' | {mensagem_alerta}'
                else:
                    df.loc[index, 'ALERTA'] = mensagem_alerta
            
    print("Verificação concluída. A coluna 'ALERTA' foi adicionada com a contagem de caracteres.")
    return df


def alertar_emails_e_telefones(df: pd.DataFrame) -> pd.DataFrame:
    """
    Verifica se o e-mail não contém o caractere '@' ou se tem espaços, e 
    se o telefone tem 8 ou 9 dígitos.
    Adiciona a mensagem de alerta à coluna 'ALERTA'.
    """
    print("\n--- Validando E-mail e Telefone ---")
    if 'EMAIL' in df.columns:
        # CORREÇÃO AQUI: Garante que a coluna 'EMAIL' é do tipo string
        df['EMAIL'] = df['EMAIL'].astype(str).str.strip()
        indices_email_invalido = df[(df['EMAIL'].str.contains(' ')) | (~df['EMAIL'].str.contains('@'))].index
        
        for index in indices_email_invalido:
            mensagem_alerta = '[EMAIL INVALIDO]'
            if df.loc[index, 'ALERTA']:
                df.loc[index, 'ALERTA'] += f' | {mensagem_alerta}'
            else:
                df.loc[index, 'ALERTA'] = mensagem_alerta
                
    if 'TELEFONE' in df.columns:
        # CORREÇÃO AQUI: Garante que a coluna 'TELEFONE' é do tipo string
        df['TELEFONE'] = df['TELEFONE'].astype(str).str.strip()
        indices_telefone_invalido = df[(df['TELEFONE'].str.len() != 8) & (df['TELEFONE'].str.len() != 9)].index

        for index in indices_telefone_invalido:
            mensagem_alerta = '[TELEFONE INVALIDO]'
            if df.loc[index, 'ALERTA']:
                df.loc[index, 'ALERTA'] += f' | {mensagem_alerta}'
            else:
                df.loc[index, 'ALERTA'] = mensagem_alerta
    
    print("Validação de e-mail e telefone concluída.")
    return df


# --- Função Auxiliar: Conexão (Centralizada para uso em ambas as funções) ---
def get_sql_connection_string():
    """Retorna a string de conexão ao banco de dados utilizando variáveis de ambiente."""
    server = os.getenv("DB_SERVER")
    database = os.getenv("DB_NAME")
    uid = os.getenv("DB_UID")
    pwd = os.getenv("DB_PWD")

    # Validação opcional para garantir que as variáveis estão preenchidas
    if not all([server, database, uid, pwd]):
        print("Aviso: Alguma variável de ambiente do banco de dados não foi encontrada.")

    return (
        f"Driver={{ODBC Driver 17 for SQL Server}};"
        f"Server={server};"
        f"Database={database};"
        f"UID={uid};"
        f"PWD={pwd};"
        f"Authentication=ActiveDirectoryPassword;"
    )

# --- FUNÇÃO 1: Verificar Clientes (Sua função original) ---
def verificar_clientes_sql(df: pd.DataFrame) -> pd.DataFrame:
    """
    Conecta-se ao banco de dados SQL e verifica se os clientes (CEPs) já estão cadastrados.
    Adiciona a coluna 'CADASTRAR_CEP' ao DataFrame.
    """
    print('\nIniciando processo de conferência de CEP com o JDe...\n')

    try:
        dados_conexao = get_sql_connection_string()

        with pyodbc.connect(dados_conexao) as conexao:
            consulta = """
                SELECT trim([cep_1]) as CEP
                FROM [dbo].[CADASTRO_cep_total]
            """
            df_cep_sql = pd.read_sql_query(consulta, conexao)

            df['CEP'] = df['CEP'].astype(str)
            df_cep_sql['CEP'] = df_cep_sql['CEP'].astype(str)

            df['CADASTRAR_CEP'] = np.where(df['CEP'].isin(df_cep_sql['CEP']), 'Já cadastrado', 'Cadastrar')

        print('Conferência de CEP com o JDe concluída e coluna CADASTRAR_CEP adicionada.')
    except Exception as e:
        print(f"Erro ao conectar ou consultar o banco de dados SQL (verificar_clientes_sql): {e}")
        df['CADASTRAR_CEP'] = 'ERRO NA VERIFICACAO'

    return df

# --- FUNÇÃO 2: Consultar Cadastro Terceiro (Nova função com a query completa) ---
def consultar_cadastro_terceiro(df: pd.DataFrame) -> pd.DataFrame:
    """
    Conecta-se ao banco de dados SQL para consultar a tabela CADASTRO_terceiro
    e adiciona todas as colunas especificadas ao DataFrame.
    """
    print('\nIniciando processo de consulta da tabela CADASTRO_terceiro (dados completos)...\n')
    
    # 🚨 COLUNA DE JUNÇÃO: Coluna no seu DataFrame (df) que corresponde a 'numero_cadastro' no SQL.
    # Por favor, ajuste se o nome for diferente no seu DataFrame de entrada.
    COLUNA_DE_JUNCAO = 'CPF' 
    
    # Colunas que serão adicionadas no DataFrame final (exceto a chave de junção)
    COLUNAS_NOVAS = [
        'id_trib', 
        'nome_alfa', 
        'tipo_pesquisa', 
        'fis_jur', 
        'codigo_26', 
        'codigo_10'
    ]

    try:
        dados_conexao = get_sql_connection_string()

        with pyodbc.connect(dados_conexao) as conexao:
            # Sua query completa
            consulta = """
                SELECT [numero_cadastro]
                     ,trim([id_trib]) as id_trib
                     ,trim([nome_alfa]) as nome_alfa
                     ,trim([tipo_pesquisa]) as tipo_pesquisa
                     ,trim([fis_jur]) as fis_jur
                     ,trim([codigo_26]) as codigo_26
                     ,trim([codigo_7]) as codigo_10 
                FROM [dbo].[CADASTRO_terceiro]
            """
            
            df_terceiro_sql = pd.read_sql_query(consulta, conexao)

            # Garante que a coluna de junção seja string em ambos
            df_terceiro_sql['id_trib'] = df_terceiro_sql['id_trib'].astype(str)
            df[COLUNA_DE_JUNCAO] = df[COLUNA_DE_JUNCAO].astype(str)
            
            # Realiza a junção (merge)
            df = df.merge(
                df_terceiro_sql, 
                left_on=COLUNA_DE_JUNCAO, 
                right_on='id_trib', 
                how='left' # Mantém todas as linhas do seu DF original
            )
            
        print('Consulta da tabela CADASTRO_terceiro concluída e todos os dados foram adicionados.')
    except Exception as e:
        print(f"Erro ao conectar ou consultar o banco de dados SQL (consultar_cadastro_terceiro): {e}")
        # Adiciona todas as novas colunas com o status de erro em caso de falha
        for col in COLUNAS_NOVAS:
            df[col] = 'ERRO NA CONSULTA'

    # 2. VALIDAÇÃO ADICIONAL (Adicionada em uma única linha)
    # Assumindo que a coluna do seu dataframe de entrada se chama 'nome'
    if 'NOME' in df.columns and 'nome_alfa' in df.columns:
        # Garante que as colunas sejam strings e remove espaços em branco antes da comparação
        df['nome_alfa'] = df['nome_alfa'].astype(str).str.strip()
        df['NOME'] = df['NOME'].astype(str).str.strip()
        
        # Validação principal usando np.where
        df['validacao_nome'] = np.where(
            df['nome_alfa'] == df['NOME'], 
            'OK', 
            'Verificar'
        )
        print("Coluna 'validacao_nome' adicionada com sucesso.")
    else:
        # Caso uma das colunas necessárias não exista no DataFrame
        print("Aviso: Colunas 'nome' ou 'nome_alfa' não encontradas para validação.")
        df['validacao_nome'] = 'COLUNAS FALTANTES'

    return df        

# --- Parte principal do script para ler, tratar e salvar o arquivo ---
# Define o diretório raiz do projeto dinamicamente
BASE_DIR = Path(__file__).resolve().parent

# Cria a pasta 'data' automaticamente se ela não existir
(BASE_DIR / "data").mkdir(exist_ok=True)

# Caminhos locais organizados dentro do projeto
caminho_do_arquivo = BASE_DIR / "data" / "biobox.xlsx"
caminho_do_arquivo_tratado = BASE_DIR / "data" / "dados_tratados.xlsx"
caminho_ibge = BASE_DIR / "data" / "ibge.xlsx"



try:
    if caminho_do_arquivo.endswith('.csv'):
        # Adicionado DDD e TELEFONE para serem lidos como strings
        dtype_dict = {'CPF': str, 'CEP': str, 'NUMERO': str, 'DDD': str, 'TELEFONE': str, 'EMAIL': str}
        df_original = pd.read_csv(caminho_do_arquivo, dtype=dtype_dict)
    elif caminho_do_arquivo.endswith('.xlsx'):
        # Adicionado DDD e TELEFONE para serem lidos como strings
        dtype_dict = {'CPF': str, 'CEP': str, 'NUMERO': str, 'DDD': str, 'TELEFONE': str, 'EMAIL': str}
        df_original = pd.read_excel(caminho_do_arquivo, dtype=dtype_dict)
    else:
        raise ValueError("Formato de arquivo não suportado. Use .csv ou .xlsx.")
     # NOVO TRECHO ADICIONADO: Converte a coluna 'EMAIL' para minúsculas

    if 'EMAIL' in df_original.columns:
        df_original['EMAIL'] = df_original['EMAIL'].astype(str).str.lower()

    print("Arquivo lido com sucesso. Iniciando o tratamento dos dados...")
    
    # 1. Tratamento inicial dos dados
    df_tratado = tratar_dataframe(df_original)

    # 2. Tratamento e validação de DDD e Telefone
    df_tratado = tratar_telefone_e_ddd(df_tratado)

    string_cols = df_tratado.select_dtypes(include=['object']).columns
    for col in string_cols:
        df_tratado[col] = df_tratado[col].str.replace(r'\s+', ' ', regex=True)

    # 3. Conciliação com a base do IBGE
    df_tratado = conciliar_com_ibge(df_tratado, caminho_ibge)

    # 4. Consulta à API dos Correios para a coluna 'UF' e verificação
    df_tratado = adicionar_uf_api(df_tratado)
        
    # 5. Verificação de clientes no banco de dados SQL
    df_tratado = verificar_clientes_sql(df_tratado)

    df_tratado = consultar_cadastro_terceiro(df_tratado)

    # 6. Verificação de caracteres longos
    df_tratado = alertar_colunas_longas(df_tratado, limite=40)
    
    # 7. Validação de e-mail e telefone (adiciona à coluna 'ALERTA')
    df_tratado = alertar_emails_e_telefones(df_tratado)

    # 8. Salvando o arquivo final
    if caminho_do_arquivo_tratado.endswith('.csv'):
        df_tratado.to_csv(caminho_do_arquivo_tratado, index=False)
    elif caminho_do_arquivo_tratado.endswith('.xlsx'):
        df_tratado.to_excel(caminho_do_arquivo_tratado, index=False)
        
    print(f"\nTratamento completo. O arquivo foi salvo como '{caminho_do_arquivo_tratado}'.")

    print("\n--- Primeiras 5 linhas do DataFrame tratado com as novas colunas ---")
    print(df_tratado.head())

except FileNotFoundError:
    print(f"Erro: O arquivo não foi encontrado no caminho '{caminho_do_arquivo}'.")
except Exception as e:
    print(f"Ocorreu um erro: {e}")

os.startfile(caminho_do_arquivo_tratado)