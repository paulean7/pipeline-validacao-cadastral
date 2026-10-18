# 🚀 Pipeline de Higienização, Validação e Enriquecimento Cadastral

Pipeline de Engenharia de Dados desenvolvido em **Python** para automatizar o tratamento, a validação de documentos, a consulta a APIs externas e a conciliação com bases relacionais (SQL Server/JDE) e planilhas de referência.

## 🛠️ Tecnologias e Bibliotecas Utilizadas
* **Linguagem:** Python 3.x
* **Manipulação e Análise de Dados:** Pandas, NumPy
* **Validação de Dados:** Validate-DocBR (CPF), Unidecode (normalização de strings)
* **Concorrência e Performance:** ThreadPoolExecutor (requisições assíncronas/paralelas)
* **Conectividade:** Requests (APIs REST), PyODBC (Banco de Dados SQL Server com Active Directory)
* **Gerenciamento de Ambiente:** Python-Dotenv, Pathlib

---

## ⚙️ Principais Funcionalidades do Pipeline

1. **Higienização de Dados (`tratar_dataframe`):** Padronização de textos para maiúsculas/minúsculas, remoção de acentos, caracteres especiais, tratamento de quebras de linha e normalização inteligente de endereços (número e complemento).
2. **Validação de Documentos e Contatos:** Validação matemática de CPFs, verificação de duplicidade, validação de formato de CEPs, e-mails e contagem de dígitos em telefones.
3. **Enriquecimento via API dos Correios (`adicionar_uf_api`):** 
   * Autenticação via token baseada em contrato com credenciais em Base64.
   * Consultas em lote otimizadas com concorrência (`ThreadPoolExecutor`) para validação e cruzamento de UFs e endereços por CEP.
4. **Conciliação com Dados Geográficos/Fiscais (`conciliar_com_ibge`):** Cruzamento de dados locais com bases de referência para resgatar alíquotas, regiões e códigos municipais.
5. **Integração com Banco de Dados Corporativo (SQL Server / JDE):**
   * Verificação de clientes já cadastrados na base de CEPs.
   * Consulta à tabela de terceiros (`CADASTRO_terceiro`) utilizando autenticação Active Directory e realizando *merges* com validação cruzada de nomes.
6. **Geração de Alertas Inteligentes:** Criação automatizada de uma coluna de auditoria (`ALERTA`) mapeando inconsistências e campos longos.

---

## 📁 Estrutura do Projeto

```text
validacao-biobox/
│
├── data/
│   ├── biobox.xlsx                 # Arquivo de entrada (Simulado/Mock)
│   ├── ibge.xlsx                   # Base de referência auxiliar
│   └── dados_tratados.xlsx         # Saída gerada após a execução do pipeline
│
├── src/
│   └── pipeline.py                 # Script principal do pipeline de dados
│
├── .env.example                    # Modelo de variáveis de ambiente necessárias
├── .gitignore                      # Arquivos e pastas ignorados pelo Git (.env, dados reais)
└── README.md                       # Documentação do projeto
```

---

## 🔧 Como Executar o Projeto

### 1. Pré-requisitos
* Python instalado na máquina.
* Drivers ODBC configurados (caso vá conectar ao SQL Server).

### 2. Instalação das Dependências
Clone o repositório e instale os pacotes necessários:
```bash
pip install pandas unidecode validate-docbr requests pyodbc numpy python-dotenv
```

### 3. Configuração do Ambiente (.env)
Crie um arquivo `.env` na raiz do projeto baseado no arquivo `.env.example` e preencha com as suas credenciais locais:
```env
CORREIOS_USUARIO=seu_usuario
CORREIOS_SENHA=sua_senha
CORREIOS_CONTRATO=seu_contrato

DB_SERVER=seu_servidor.database.windows.net
DB_NAME=nome_do_banco
DB_UID=seu_email@dominio.com.br
DB_PWD=sua_senha
```

### 4. Executando o Script
Com os arquivos de entrada posicionados na pasta `data/`, execute o script a partir da raiz do projeto:
```bash
python src/pipeline.py
```