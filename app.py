from flask import Flask, render_template, request, redirect, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import extract
from datetime import datetime, timedelta, date

from werkzeug.security import check_password_hash
from functools import wraps

import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()


app = Flask(__name__)
app.secret_key=os.getenv('MINHA_CHAVE_SECRETA')
SENHA_HASH=os.getenv('MINHA_SENHA_SECRETA')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('autenticado'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS SQLite
# ==========================================
basedir = os.path.abspath(os.path.dirname(__file__))

app.config['SQLALCHEMY_DATABASE_URI'] = (
    'sqlite:///' + os.path.join(basedir, 'instance', 'ponto.db')
)
db = SQLAlchemy(app)

# Modelo para salvar os registros
class Registro(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False)
    entrada = db.Column(db.DateTime, nullable=False)
    saida = db.Column(db.DateTime, nullable=False)
    total_segundos = db.Column(db.Integer)
    total_segundos_extra = db.Column(db.Integer)
    extra_100 = db.Column(db.Boolean)

# Modelo para salvar configurações de salário e turno
class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    salario = db.Column(db.Float)
    periculosidade = db.Column(db.Boolean)
    adicional_noturno = db.Column(db.Integer)
    hora_entrada = db.Column(db.Time, nullable=False)
    hora_saida = db.Column(db.Time, nullable=False)

def fetch_db_records(periodo: str):
    ano, mes = map(int, periodo.split('-'))

    registros = Registro.query.filter(
        extract('year', Registro.data) == ano,
        extract('month', Registro.data) == mes
    ).all()

    return [{
        "data_registro": r.data,
        "horas_trabalhadas": r.total_segundos,
        "horas_extras": r.total_segundos_extra,
        "extra_100_porcento": r.extra_100
    } for r in registros]

def executar_auditoria_folha(caminho_pdf, periodo_alvo):

    #Holerite PDF Upload
    sample_file = genai.upload_file(path=caminho_pdf, display_name="holerite")
    #Dados de registro do banco de dados
    registros_banco = fetch_db_records(periodo_alvo)

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel('gemini-2.5-flash')

    response = model.generate_content([
        sample_file,
        "\n\n",
        f"Faça uma comparação dos registros que eu tenho de marcação de ponto com o holerite em PDF anexado (sample_file). Registros do banco: {registros_banco}. Verifique se as horas registradas no banco conferem com as horas pagas no holerite, considerando o salário base e os adicionais legais (CLT brasileira). Gere um relatório detalhado apontando quaisquer divergências encontradas, bem como um resumo final indicando se tudo está OK ou se há discrepâncias a serem corrigidas. Considere também que o holerite fecha as horas extras no dia 15 de cada mês."
    ])

    print(response.text)
    return response.text

def contar_domingos(data_inicio, data_fim):
    domingos = 0
    data_atual = data_inicio

    while data_atual <= data_fim:
        if data_atual.weekday() == 6:  # domingo
            domingos += 1
        data_atual += timedelta(days=1)

    return domingos

# ==========================================
# ROTAS
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        senha_digitada = request.form.get('senha')

        # Compara o hash guardado com a senha que o usuário digitou
        if check_password_hash(SENHA_HASH, senha_digitada):
            session['autenticado'] = True
            return redirect(url_for('index'))

        return "Senha inválida!", 401

    return '''
        <form method="post">
            <input type="password" name="senha" placeholder="Senha">
            <button type="submit">Entrar</button>
        </form>
    '''
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':

        # Busca o primeiro (e único) registro de configuração
        config_atual = Config.query.first()

        data_str = request.form['data']
        ent_str = f"{data_str} {request.form['entrada']}"
        sai_str = f"{data_str} {request.form['saida']}"
        extra = request.form.get('extra')
        trocado = request.form.get('trocado')

        formato = '%Y-%m-%d %H:%M'
        entrada = datetime.strptime(ent_str, formato)
        saida = datetime.strptime(sai_str, formato)

        # Lógica para turno noturno (virada do dia)
        if saida <= entrada:
            saida += timedelta(days=1)

        diff = saida - entrada

        #
        conf_ent_full = datetime.combine(entrada.date(), config_atual.hora_entrada)
        conf_sai_full = datetime.combine(entrada.date(), config_atual.hora_saida)
        # 2. Se o turno configurado vira o dia (ex: 22h às 06h), ajustamos a saída da config
        if conf_sai_full <= conf_ent_full:
            conf_sai_full += timedelta(days=1)
        # 3. Inicializamos a variável de segundos extras como zero (em formato timedelta)
        total_extra_td = timedelta(0)

        is_100 = False
        if extra:
            total_extra_td = diff
            is_100 = True
        else:
            is_100 = False
            # Entrada antecipada (mais de 5 min antes)
            if trocado:
                print(total_extra_td)
                if entrada < (conf_sai_full - timedelta(minutes=5)):
                    total_extra_td += (conf_sai_full.replace(year=2000, month=1, day=1) - entrada.replace(year=2000, month=1, day=1))

                # Saída tardia (mais de 5 min depois)
                if saida > (conf_ent_full + timedelta(minutes=5)):
                    total_extra_td += (saida.replace(year=2000, month=1, day=1) - conf_ent_full.replace(year=2000, month=1, day=1))

            else:
                if entrada < (conf_ent_full - timedelta(minutes=5)):
                    total_extra_td += (conf_ent_full - entrada)
                # Saída tardia (mais de 5 min depois)
                if saida > (conf_sai_full + timedelta(minutes=5)):
                    total_extra_td += (saida - conf_sai_full)
        print(total_extra_td)
        novo_registro = Registro(
            data=datetime.strptime(data_str, '%Y-%m-%d').date(),
            entrada=entrada,
            saida=saida,
            total_segundos=int(diff.total_seconds()),
            total_segundos_extra=int(total_extra_td.total_seconds()),
            extra_100=is_100
        )
        db.session.add(novo_registro)
        db.session.commit()
        return redirect('/')

    # Cálculo do total mensal (Mês atual de 2025)
    hoje = datetime.now()
    mes_selecionado = hoje.month
    ano_selecionado = hoje.year

    # 1. Ajuste do Intervalo de Datas (16 do anterior ao 15 do atual)
    data_fim = datetime(ano_selecionado, mes_selecionado, 15).date()
    if mes_selecionado == 1:
        data_inicio = datetime(ano_selecionado - 1, 12, 16).date()
    else:
        data_inicio = datetime(ano_selecionado, mes_selecionado - 1, 16).date()

    registros_mes = Registro.query.filter(
        Registro.data >= data_inicio,
        Registro.data <= data_fim
    ).all()

    total_geral_segundos = sum(r.total_segundos for r in registros_mes)
    horas_totais = total_geral_segundos // 3600
    minutos_totais = (total_geral_segundos % 3600) // 60

    return render_template('index.html',
                           registros=registros_mes,
                           total=f"{horas_totais}h {minutos_totais}min")

@app.route('/delete/<int:id>')
@login_required
def deleteRegister(id):
    # Busca o registro pelo ID ou retorna erro 404 se não existir
    registro = Registro.query.get_or_404(id)

    try:
        db.session.delete(registro)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # Opcional: imprimir erro no console ou flash message
        print(f"Erro ao deletar: {e}")

    return redirect(url_for('index'))

@app.route('/config', methods=['GET', 'POST'])
@login_required
def config():
    # Busca o primeiro (e único) registro de configuração
    config_atual = Config.query.first()

    if request.method == 'POST':
        # Captura dados do formulário
        valor_salario = float(request.form.get('salario', 0))
        tem_periculosidade = 'periculosidade' in request.form
        porcent_noturno = int(request.form.get('adicional_noturno', 0))
        h_entrada = datetime.strptime(request.form['hora_entrada'], '%H:%M').time()
        h_saida = datetime.strptime(request.form['hora_saida'], '%H:%M').time()

        if not config_atual:
            # Se não existe, cria um novo
            config_atual = Config(salario=valor_salario, periculosidade=tem_periculosidade, adicional_noturno = porcent_noturno, hora_entrada = h_entrada, hora_saida = h_saida)
            db.session.add(config_atual)
        else:
            # Se existe, altera o atual
            config_atual.salario = valor_salario
            config_atual.periculosidade = tem_periculosidade
            config_atual.adicional_noturno = porcent_noturno
            config_atual.hora_entrada = h_entrada
            config_atual.hora_saida = h_saida

        db.session.commit()
        return redirect('/') # Redireciona para a home após salvar

    return render_template('config.html', config=config_atual)

@app.route('/relatorio', methods=['GET', 'POST'])
@login_required
def relatorio():
    hoje = datetime.now()
    mes_selecionado = int(request.form.get('mes_selecionado', hoje.month))
    ano_selecionado = int(request.form.get('ano_selecionado', hoje.year))

    # 1. Ajuste do Intervalo de Datas (16 do anterior ao 15 do atual)
    data_fim = datetime(ano_selecionado, mes_selecionado, 15).date()
    if mes_selecionado == 1:
        data_inicio = datetime(ano_selecionado - 1, 12, 16).date()
    else:
        data_inicio = datetime(ano_selecionado, mes_selecionado - 1, 16).date()

    config = Config.query.first()
    registros = Registro.query.filter(
        Registro.data >= data_inicio,
        Registro.data <= data_fim
    ).all()

    # Funções para calcular INSS e IRPF com base nas tabelas de 2025

    def calcular_inss(salario_bruto):
        """Calcula o INSS progressivo para 2025."""
        if salario_bruto <= 1518.00:
            return salario_bruto * 0.075, 0.075 #
        elif salario_bruto <= 2793.88:
            return (salario_bruto * 0.09) - 22.77, 0.09 #
        elif salario_bruto <= 4190.83:
            return (salario_bruto * 0.12) - 106.59, 0.12 #
        elif salario_bruto <= 8157.41:
            return (salario_bruto * 0.14) - 190.40, 0.14 #
        else:
            return 8157.41 * 0.14 - 190.40, 0.14 # Teto do INSS

    def calcular_irpf(salario_bruto, inss_descontado):
        """Calcula o IRPF progressivo para 2025."""
        base_calculo = salario_bruto - inss_descontado

        if base_calculo <= 2259.20:
            return 0.0, 0.0 # Isento
        elif base_calculo <= 2826.65:
            return (base_calculo * 0.075) - 169.44, 0.075 #
        elif base_calculo <= 3751.05:
            return (base_calculo * 0.15) - 381.44, 0.15 #
        elif base_calculo <= 4664.68:
            return (base_calculo * 0.225) - 662.77, 0.225 #
        else:
            return (base_calculo * 0.275) - 975.80, 0.275 # Alíquota máxima

    # Variáveis de Acúmulo
    t_segundos_total = 0
    t_segundos_noturnos = 0
    t_segundos_extra_100 = 0
    t_segundos_extra_50 = 0
    dias_trabalhados = set()

    for r in registros:
        #dias trabalhados
        dias_trabalhados.add(r.data)
        # Acúmulo de Segundos Totais
        t_segundos_total += r.total_segundos

        # Separação de Horas Extras (50% vs 100%)
        if r.extra_100:
            t_segundos_extra_100 += r.total_segundos_extra
        else:
            t_segundos_extra_50 += r.total_segundos_extra

        # Cálculo Simplificado de Horas Noturnas (22h às 05h)
        # Se a saída for após as 22h ou entrada antes das 05h
        ent = r.entrada
        sai = r.saida
        limite_noite = ent.replace(hour=22, minute=0, second=0)

        if sai > limite_noite:
            segundos_pos_22 = (sai - max(ent, limite_noite)).total_seconds()
            t_segundos_noturnos += max(0, segundos_pos_22)

    # Conversões para horas decimais
    h_total = t_segundos_total / 3600
    h_noturna = h_noturna = (t_segundos_noturnos / 3600) * 1.142857
    h_extra_50 = t_segundos_extra_50 / 3600
    h_extra_100 = t_segundos_extra_100 / 3600
    #dias trabalhados
    qtd_dias_trabalhados = len(dias_trabalhados)
    domingos = contar_domingos(data_inicio, data_fim)


    # Cálculos Financeiros
    v_hora = config.salario if config else 0
    v_base = (h_total - h_extra_100 - h_extra_50) * v_hora

    # Cálculo das Horas Extras Financeiro (CLT 2025)
    # 50%: valor_hora * 1.5 | 100%: valor_hora * 2.0
    v_extra_50 = h_extra_50 * (v_hora * 1.5)
    v_extra_100 = h_extra_100 * (v_hora * 2.0)

    v_peri = 0
    if config and config.periculosidade:
        v_peri = v_base * 0.30

    v_noturno = 0
    if config:
        # Adicional noturno sobre as horas trabalhadas no período
        v_noturno = h_noturna * v_hora * (config.adicional_noturno / 100)

    #DSR sobre horas normais
    dsr_horas = 0
    dsr_valor = 0

    if qtd_dias_trabalhados > 0:
        dsr_horas = (h_total / qtd_dias_trabalhados) * domingos
        dsr_valor = dsr_horas * v_hora
    #DSR sobre adicional noturno
    dsr_noturno = 0

    if qtd_dias_trabalhados > 0:
        dsr_noturno = (h_noturna / qtd_dias_trabalhados) * domingos
        dsr_noturno_valor = dsr_noturno * v_hora * (config.adicional_noturno / 100)

    #DSR sobre horas extras
    dsr_extra_50 = 0
    dsr_extra_100 = 0

    if qtd_dias_trabalhados > 0:
        dsr_extra_50 = (h_extra_50 / qtd_dias_trabalhados) * domingos
        dsr_extra_50_valor = dsr_extra_50 * (v_hora * 1.5)
        dsr_extra_100 = (h_extra_100 / qtd_dias_trabalhados) * domingos
        dsr_extra_100_valor = dsr_extra_100 * (v_hora * 2.0)


    #Calcular Salário Bruto Total para Descontos
    salario_bruto = (
        v_base +
        dsr_valor +
        v_peri +
        v_noturno +
        dsr_noturno_valor +
        v_extra_50 +
        dsr_extra_50_valor +
        v_extra_100 +
        dsr_extra_100_valor
    )


    #Aplicar Descontos
    v_inss, aliq_inss = calcular_inss(salario_bruto)
    v_irpf, aliq_irpf = calcular_irpf(salario_bruto, v_inss)
    v_fgts = salario_bruto * 0.08 # (Não é desconto, mas aparece no holerite)

    total_descontos = v_inss + v_irpf
    v_liquido = salario_bruto - total_descontos

    return render_template('relatorio.html',  **locals())

@app.route('/auditoria', methods=['GET', 'POST'])
@login_required
def auditoria():
    if request.method == 'POST':
        caminho_pdf = request.form['caminho_pdf']
        periodo_alvo = request.form['periodo_alvo']
        resultado = executar_auditoria_folha(caminho_pdf, periodo_alvo)
        return render_template('auditoria.html', resultado=resultado)
    return render_template('auditoria.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # O Render define a variável de ambiente PORT automaticamente
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

