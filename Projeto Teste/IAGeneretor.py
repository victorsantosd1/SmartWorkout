from flask import Flask, render_template, request, session, url_for, redirect
import google.generativeai as genai
import json
import os
from flask_cors import CORS
import libsql_client 
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "123"
CORS(app)


TURSO_URL = "libsql://smartworkout-philipe.turso.io"
TURSO_AUTH_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3MzI1NzQ3MzQsImlkIjoiMzhhNzUyNDYtNWZjYi00NGY0LWIxMzItZjJmZDExMWQ5ZDczIn0.H8xiuEVjgGFrRqXFd2q77jZCvW5xsP5KCmhkoYTLdg_MTWKSD29ehWRaeZkAGuviwYLwVuZqzdqPPi4XoYjoAw"

genai.configure(api_key='AIzaSyAf0CRdfO_O8Jis8CberlddhBRt5VDCdQg')  # Substitua pela sua chave de API

def calcular_imc(peso, altura):
    return peso / (altura ** 2)

def salvar_treino_no_banco(treino_json):
    print(treino_json)
    """
    Salva os dados do treino no banco de dados, dividindo entre as tabelas Treino e Exercicios.
    """
    try:
        with libsql_client.create_client_sync(
            TURSO_URL,
            auth_token=TURSO_AUTH_TOKEN
        ) as client:
            # Inserir na tabela Treino e obter o ID do treino inserido
            for treino in treino_json["divisao"]:
        
                treino_id = client.execute(
                    "INSERT INTO Treino (Grupo, Usuario_ID) VALUES (?, ?) RETURNING ID_Treino",
                    [treino["grupo"], session['id']]
                )[0]["ID_Treino"]  # Presume que o banco retorna o ID do treino inserido
            
                # Inserir os exercícios associados ao treino
                for exercicio in treino["exercicios"]:
                    nome_exercicio = exercicio["nome"]
                    series = exercicio["series"]
                    repeticoes = exercicio["repeticoes"]
                    
                    # Inserir cada exercício na tabela Exercicios
                    client.execute(
                        "INSERT INTO Exercicio (Nome_Exercicio, Series, Repeticoes, Treino_ID) VALUES (?, ?, ?, ?)",
                        [nome_exercicio, series, repeticoes, treino_id]
                    )
        print(f"Treino salvo com sucesso no banco. Treino_ID: {treino_id}")
    except Exception as e:
        print(f"Erro ao salvar treino no banco: {e}")


def buscar_treino_no_banco(usuario_id):
    """
    Busca todos os treinos de um usuário no banco de dados e os exercícios associados.
    Retorna uma estrutura de dados consolidada.
    """
    try:
        with libsql_client.create_client_sync(
            TURSO_URL,
            auth_token=TURSO_AUTH_TOKEN
        ) as client:
        # Buscar todos os treinos do usuário
            treinos = client.execute("SELECT ID_Treino, Grupo FROM Treino WHERE Usuario_ID = ?", [usuario_id])

            if not treinos:
                return None  # Nenhum treino encontrado para o usuário

            resultado = []
            for treino in treinos:
                treino_id = treino["ID_Treino"]
                grupo = treino["Grupo"]

                # Buscar todos os exercícios associados a este treino
                exercicios = client.execute(
                    "SELECT Nome_Exercicio, Series, Repeticoes FROM Exercicio WHERE Treino_ID = ?", [treino_id]
                )

                # Montar estrutura consolidada
                resultado.append({
                    "grupo": grupo,
                    "exercicios": [
                        {
                            "nome": exercicio["Nome_Exercicio"],
                            "series": exercicio["Series"],
                            "repeticoes": exercicio["Repeticoes"]
                        }
                        for exercicio in exercicios
                    ]
                })

        return resultado

    except Exception as e:
        print(f"Erro ao buscar treinos no banco: {e}")
        return None


def validar_e_corrigir_json(texto):
    """
    Valida e tenta corrigir o JSON gerado pela IA.
    """
    try:
        return json.loads(texto)  # Tenta carregar o JSON diretamente
    except json.JSONDecodeError:
        # Remove possíveis caracteres extras e tenta novamente
        texto_corrigido = texto.strip().replace("```json", "").replace("```", "")
        try:
            return json.loads(texto_corrigido)
        except json.JSONDecodeError as e:
            print("Erro ao corrigir JSON:", e)
            return None

def gerar_sugestoes_treino(nome, model, objetivo, imc, nivel_experiencia, idade, tipo_corporal, tempo_objetivo):
    prompt = f"""
    Crie um plano de treino em formato **estritamente JSON**. Não adicione texto explicativo, somente o JSON puro.
    Características da pessoa:
    - Idade: {idade} anos
    - Objetivo: {objetivo}
    - IMC: {imc:.2f}
    - Nível de experiência: {nivel_experiencia}
    - Tipo corporal: {tipo_corporal}
    - Tempo para alcançar o objetivo: {tempo_objetivo}
    - crie 4 planos de treinos dividido em treino A, B, C e D dentro de um array

    O JSON deve ter as seguintes chaves principais:
    - "aquecimento" (string com descrição)
    - "resfriamento" (string com descrição)
    - "frequencia_treino" (número indicando frequência semanal)
    - "divisao" (um array com os treinos:
      - "grupo" (string com nome do grupo muscular)
      - "exercicios" (uma lista com objetos: "nome", "series", "repeticoes em string")).
    """

    texto_da_IA = ""
    treino_salvo = None

    try:
        response = model.generate_content(prompt)
        texto_da_IA = response.text.strip()

        treino = validar_e_corrigir_json(texto_da_IA)  # Validação e correção do JSON
        if treino:
            treino_salvo = salvar_treino_no_banco(treino)
        else:
            print("JSON inválido após tentativa de correção.")

    except Exception as e:
        print(f"Erro ao chamar a IA: {e}")

    return texto_da_IA, treino_salvo


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        nome = request.form['nome']
        idade = int(request.form['idade'])
        altura = float(request.form['altura'])
        peso = float(request.form['peso'])
        objetivo_num = int(request.form['objetivo'])
        experiencia_num = int(request.form['experiencia'])
        tipo_corporal_num = int(request.form['tipo_corporal'])
        tempo_objetivo = request.form['tempo_objetivo']

        # Mapear valores
        objetivos = {1: "Hipertrofia", 2: "Definição muscular", 3: "Saúde e bem-estar"}
        objetivo = objetivos.get(objetivo_num, "Saúde e bem-estar")

        experiencia_map = {
            1: ("iniciante (nunca treinou)", 4),
            2: ("iniciante (1 a 3 meses)", 4),
            3: ("intermediário (6 a 1 ano)", 5),
            4: ("intermediário (voltando a treinar)", 5)
        }
        nivel_experiencia, min_exercicios = experiencia_map.get(experiencia_num, ("iniciante (nunca treinou)", 4))

        tipos_corporais = {1: "Ectomorfo", 2: "Mesomorfo", 3: "Endomorfo"}
        tipo_corporal = tipos_corporais.get(tipo_corporal_num, "")

        imc = calcular_imc(peso, altura)

        # Configuração da IA
        generation_config = {"candidate_count": 1, "temperature": 0.5, "top_p": 0.5, "top_k": 50}
        safety_settings = {'HARASSMENT': 'BLOCK_LOW_AND_ABOVE', "HATE": 'BLOCK_LOW_AND_ABOVE', "SEXUAL": 'BLOCK_LOW_AND_ABOVE', "DANGEROUS": 'BLOCK_LOW_AND_ABOVE'}

        model = genai.GenerativeModel(model_name="gemini-1.0-pro", generation_config=generation_config, safety_settings=safety_settings)

        # Gerar o treino com a IA
        sugestao_texto, sugestoes_treino_json = gerar_sugestoes_treino(
            nome, model, objetivo, imc, nivel_experiencia, idade, tipo_corporal, tempo_objetivo
        )

        # Caso a IA falhe ao gerar o plano
        if not sugestoes_treino_json:
            erro = "O plano de treino não pôde ser gerado. Tente novamente."
            return render_template('treino.html', nome=nome, imc=imc, treino=None, erro=erro)

        # Exibir o treino gerado imediatamente
        treino_gerado = validar_e_corrigir_json(sugestao_texto)  # Usar o JSON retornado pela IA
        if treino_gerado:
            salvar_treino_no_banco(treino_gerado)  # Salvar no banco após exibição
        else:
            erro = "Erro ao processar o treino gerado. Tente novamente."
            return render_template('treino.html', nome=nome, imc=imc, treino=None, erro=erro)

        return render_template('treino.html', nome=nome, imc=imc, treino=treino_gerado)

    return render_template('inicial.html')


@app.route('/treinos', methods=['GET'])
def treinos():
    id = session['id']
    Nome = session['nome']
    print(id, Nome)
    treinos = buscar_treino_no_banco(id)
    return render_template('treino.html', nome=Nome, treinos=treinos)

@app.route('/index')
def index_page():
    return render_template("index.html")

@app.route('/cadastro', methods=['POST'])
def cadastro():
    nome = request.form['nome']
    email = request.form['email']
    senha = request.form['senha']

    try:
        senha_hash = generate_password_hash(senha)
        with libsql_client.create_client_sync(
            TURSO_URL,
            auth_token=TURSO_AUTH_TOKEN
        ) as client:
        # Inserir os dados no banco de dados
            query = """
            INSERT INTO Usuario (Nome, Email, Senha)
            VALUES (?, ?, ?)
            """
            client.execute(query, (nome, email, senha_hash))
            return render_template('inicial.html')
    except Exception as e:
            return f"Erro ao cadastrar usuário: {str(e)}", 500

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    senha = request.form['senha']

    try:
        with libsql_client.create_client_sync(
            TURSO_URL,
            auth_token=TURSO_AUTH_TOKEN
        ) as client:
            result = client.execute(
                "SELECT senha, ID_Usuario, Nome FROM Usuario WHERE Email = ?", [email]
            )

            rows = result.rows
            if not rows:
                return "Usuário não encontrado.", 404

            # Validar senha
            senha_hash = rows[0][0]
            ID = rows[0][1]
            Nome = rows[0][2]
            if check_password_hash(senha_hash, senha):
                session['is_logged_in'] = True  # Marca o usuário como logado
                session['id'] = ID
                session['nome'] = Nome
                return render_template('inicial.html')
            else:
                return "Senha incorreta.", 401
    except Exception as e:
        return f"Erro ao validar login: {str(e)}", 500   

@app.route('/logout')
def logout():
    session.pop('is_logged_in', None)  # Remove a sessão de login
    session.pop('id', None)
    session.pop('nome', None)
    
    
    return render_template('inicial.html')


if __name__ == '__main__':
    app.run(debug=True)
