import json
import time
import pymysql
import requests
import smtplib
import pytz
import paho.mqtt.client as mqtt
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================
# 1. CONFIGURAÇÕES
# ==========================================
MQTT_BROKER = "XX.XXX.XXX.XXX"
MQTT_PORT = XXXX
TOPICO_SENSORES = "sirius/estufa/sensores"
TOPICO_DASHBOARD = "sirius/estufa/dados"

DB_HOST = "XXXXXXXXXXX"
DB_PORT = XXXX
DB_USER = "XXX"
DB_PASS = "XXXXXX"
DB_NAME = "XXXXX"

API_KEY_WEATHER = "XXXXXX"
DLI_META = 15.0

# EMAIL DE ALERTA
EMAIL_REMETENTE = "XXXXXXXX"
EMAIL_SENHA_APP = "XXXXXX"
EMAIL_DESTINO = "XXXXXXXXX"

# ==========================================
# VARIÁVEIS DE MEMÓRIA E ESTADO
# ==========================================
ultima_consulta_clima = 0
vai_chover_cache = False
estado_anterior = None

estado_alerta_ativo = False
ultimo_email_enviado = 0
COOLDOWN_REFORCO = 3600  # 1 hora para e-mail de reforço


# ==========================================
# 2. BANCO DE DADOS E API DE CLIMA
# ==========================================
def conectar_banco():
    try:
        return pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=True
        )
    except Exception as e:
        print(f"🔴 Erro MySQL: {e}")
        return None


db_conn = conectar_banco()


def verificar_previsao_chuva(cidade_alvo="Manaus,BR"):
    """Consulta a API OpenWeatherMap utilizando a cidade informada pelo pacote JSON."""
    global ultima_consulta_clima, vai_chover_cache
    agora = time.time()

    # Consulta a cada 20 minutos (1200s) para evitar estouro da cota da API
    if agora - ultima_consulta_clima > 1200:
        if not API_KEY_WEATHER or API_KEY_WEATHER == "COLE_SUA_CHAVE_AQUI":
            return False

        url = f"http://api.openweathermap.org/data/2.5/forecast?q={cidade_alvo}&appid={API_KEY_WEATHER}&cnt=2"
        try:
            resposta = requests.get(url, timeout=5).json()
            clima = resposta['list'][0]['weather'][0]['main']
            vai_chover_cache = clima in ["Rain", "Thunderstorm", "Drizzle"]
            ultima_consulta_clima = agora
            print(f"☁️ [API Clima para {cidade_alvo}]: {'Chuva prevista' if vai_chover_cache else 'Sem chuva'}")
        except Exception as e:
            print(f"⚠️ Erro na consulta da API de Clima: {e}")
            vai_chover_cache = False

    return vai_chover_cache


# ==========================================
# 3. GERENCIADOR DE ALERTAS E EMAIL
# ==========================================
def enviar_email(assunto, texto):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = EMAIL_DESTINO
        msg['Subject'] = assunto
        msg.attach(MIMEText(texto, 'plain'))

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(EMAIL_REMETENTE, EMAIL_SENHA_APP)
        server.send_message(msg)
        server.quit()
        print(f"📧 EMAIL ENVIADO: {assunto}")
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")


def gerenciar_alertas(motivo_alerta):
    global estado_alerta_ativo, ultimo_email_enviado
    agora = time.time()

    if motivo_alerta != "Operação Normal":
        if not estado_alerta_ativo:
            print(f"🚨 NOVO ALERTA REGISTRADO: {motivo_alerta}")
            enviar_email("🚨 URGENTE: Novo Alerta Crítico",
                         f"Anomalia detectada: {motivo_alerta}\nVerifique o Dashboard.")
            estado_alerta_ativo = True
            ultimo_email_enviado = agora
        else:
            if agora - ultimo_email_enviado >= COOLDOWN_REFORCO:
                print(f"⚠️ REFORÇO DE ALERTA: Problema persiste por mais de 1 hora.")
                enviar_email("⚠️ REFORÇO: Alerta Persistente na Estufa",
                             f"O problema continua ativo: {motivo_alerta}")
                ultimo_email_enviado = agora
    else:
        if estado_alerta_ativo:
            print("✅ Problema resolvido! Sistema normalizado. Resetando alertas.")
            enviar_email("✅ RESOLVIDO: Estufa Normalizada",
                         "Os sensores indicam que o problema foi corrigido e a operação voltou ao normal.")
            estado_alerta_ativo = False


# ==========================================
# 4. MOTOR DE ANOMALIAS E IA PREDITIVA
# ==========================================
def detectar_anomalias(dados_atuais, is_day):
    global estado_anterior
    alertas, emergencia = [], {}
    temp = dados_atuais.get("temperatura", 25.0)
    solo = dados_atuais.get("umidade_solo", 50.0)
    uv = dados_atuais.get("uv", 0)

    if solo < 0 or solo > 110:
        alertas.append("⚠️ SENSOR FAULT (Solo)")
    if not is_day and uv > 10:
        alertas.append("⚠️ SENSOR FAULT (UV Noturno)")

    if estado_anterior is not None:
        delta_temp = temp - estado_anterior.get("temperatura", temp)
        if delta_temp > 8.0 or temp >= 55.0:
            alertas.append("🔥 INCÊNDIO / CALOR EXTREMO!")
            emergencia.update({
                'domo_aberto': True,
                'irrigacao_ativa': True,
                'luz_artificial': False,
                'status_sistema': "MODO EMERGÊNCIA"
            })
        elif delta_temp < -15.0:
            alertas.append("⚠️ SENSOR FAULT (Queda térmica)")

    estado_anterior = dados_atuais.copy()
    return alertas, emergencia


def processar_dados_ia(dados_brutos):
    # Extração de Localização e Fuso enviados pelo Colab/ESP
    cidade = dados_brutos.get("cidade", "Manaus")
    bairro = dados_brutos.get("bairro", "Centro")
    fuso_str = dados_brutos.get("fuso_horario", "America/Manaus")

    # Cálculo da Hora Local
    try:
        fuso_tz = pytz.timezone(fuso_str)
        agora_local = datetime.now(fuso_tz)
    except Exception:
        agora_local = datetime.now()

    hora, minuto = agora_local.hour, agora_local.minute
    is_day = 6 <= hora < 18

    # Processa Anomalias e Previsão
    alertas_anomalia, modo_emergencia = detectar_anomalias(dados_brutos, is_day)
    chuva_prevista = verificar_previsao_chuva(f"{cidade},BR")

    status_sistema = "Operação Normal" if not alertas_anomalia else " | ".join(alertas_anomalia)

    temp = dados_brutos.get("temperatura", 25.0)
    solo = dados_brutos.get("umidade_solo", 50.0)
    lum = dados_brutos.get("luminosidade", 0)
    uv = dados_brutos.get("uv", 0)
    dli = dados_brutos.get("dli", 0.0)
    chovendo = dados_brutos.get("chovendo", False)

    # -------------------------------------------------------------
    # CÁLCULOS DE STATUS INTERATIVOS (LUMINOSIDADE, UV E DLI)
    # -------------------------------------------------------------
    status_luminosidade = "Sombreamento Alto" if lum < 30 else ("Alta Incidência Solar" if lum > 80 else "Luminosidade Adequada")
    
    if uv < 3:
        status_uv = "Baixo (Seguro)"
    elif uv < 6:
        status_uv = "Moderado"
    elif uv < 8:
        status_uv = "Alto (Atencao)"
    else:
        status_uv = "Extremo / Anomalia"

    porcentagem_dli = round((dli / DLI_META) * 100) if DLI_META > 0 else 0
    status_dli = f"{porcentagem_dli}% da meta diária atingida"

    # -------------------------------------------------------------
    # DECISÕES E STATUS DINÂMICOS DOS ATUADORES
    # -------------------------------------------------------------
    # 1. Irrigação Preditiva
    irrigacao_ativa = (solo < 35.0) and not (chuva_prevista and not chovendo)
    if irrigacao_ativa:
        status_irrigacao = "Ativa (Solo Seco < 35%)"
    elif solo < 35.0 and chuva_prevista:
        status_irrigacao = "Pausada (Chuva Prevista)"
    elif solo >= 85.0:
        status_irrigacao = "Desligada (Solo Encharcado)"
    else:
        status_irrigacao = "Desligada (Solo Mantido)"

    # 2. Domo Dinâmico
    domo_aberto = True
    status_domo = "Aberto (Ventilação Normal)"
    if chovendo:
        domo_aberto = (solo < 70.0)
        status_domo = "Aberto (Captando Chuva)" if domo_aberto else "Fechado (Proteção Solo)"
    elif temp < 16.0:
        domo_aberto = False
        status_domo = "Fechado (Proteção Frio Extremo)"
    elif not is_day and temp < 20.0:
        domo_aberto = False
        status_domo = "Fechado (Isolamento Noturno)"

    # 3. Luz Artificial
    luz = (is_day and lum < 40 and dli < DLI_META) or (not is_day and dli < DLI_META and hora < 22)
    if luz:
        if is_day:
            status_luz = "Ligada (Complemento Diurno)"
        else:
            status_luz = "Ligada (Suplementação Noturna)"
    else:
        if dli >= DLI_META:
            status_luz = "Desligada (Meta DLI Atingida)"
        elif hora >= 22:
            status_luz = "Desligada (Descanso Vegetativo)"
        else:
            status_luz = "Desligada (Sol Suficiente)"

    # Sobrescrita de Segurança para Modo Emergência
    if modo_emergencia:
        if 'domo_aberto' in modo_emergencia:
            domo_aberto, status_domo = modo_emergencia['domo_aberto'], "ABERTO (EXAUSTÃO TÉRMICA)"
        if 'irrigacao_ativa' in modo_emergencia:
            irrigacao_ativa, status_irrigacao = modo_emergencia['irrigacao_ativa'], "LIGADA (SUPRESSÃO / RESFRIAMENTO)"
        if 'luz_artificial' in modo_emergencia:
            luz, status_luz = modo_emergencia['luz_artificial'], "CORTE ELÉTRICO DE SEGURANÇA"
        if 'status_sistema' in modo_emergencia:
            status_sistema = modo_emergencia['status_sistema']

    return {
        "cidade": cidade,
        "bairro": bairro,
        "localizacao": f"{cidade}, {bairro}",
        "temperatura": temp,
        "status_temp": "Frio" if temp < 20 else ("Quente" if temp > 30 else "Ideal"),
        "umidade_solo": solo,
        "status_umidade_solo": "Seco" if solo < 35 else ("Encharcado" if solo > 85 else "Ideal"),
        "luminosidade": lum,
        "status_luminosidade": status_luminosidade,
        "uv": uv,
        "status_uv": status_uv,
        "dli": round(dli, 1),
        "dli_meta": DLI_META,
        "status_dli": status_dli,
        "chovendo": chovendo,
        "domo_aberto": domo_aberto,
        "status_domo": status_domo,
        "luz_artificial": luz,
        "status_luz_artificial": status_luz,
        "irrigacao_ativa": irrigacao_ativa,
        "status_irrigacao": status_irrigacao,
        "alertas_sistema": status_sistema,
        "hora": hora,
        "minuto": minuto
    }


# ==========================================
# 5. MQTT E BANCO DE DADOS
# ==========================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("🟢 IA Ativa: Sistema Smart de Alertas pronto.")
        client.subscribe(TOPICO_SENSORES)
    else:
        print(f"🔴 Falha de conexão MQTT, código: {rc}")


def on_message(client, userdata, msg):
    global db_conn
    try:
        dados_brutos = json.loads(msg.payload.decode())
        dados_processados = processar_dados_ia(dados_brutos)

        # 1. Envia pacote pronto para o Dashboard Frontend
        client.publish(TOPICO_DASHBOARD, json.dumps(dados_processados), retain=True)

        # 2. Gerencia alertas e disparos de E-mail
        gerenciar_alertas(dados_processados["alertas_sistema"])

        # 3. Salva no RDS MySQL
        if db_conn is None or not db_conn.open:
            db_conn = conectar_banco()

        if db_conn:
            with db_conn.cursor() as cursor:
                sql = """
                      INSERT INTO leituras_estufa (cidade, bairro, hora_simulada, minuto_simulado, \
                                                   temperatura, status_temp, umidade_solo, status_umidade_solo, \
                                                   luminosidade, status_luminosidade, uv, status_uv, \
                                                   dli, dli_meta, status_dli, chovendo, \
                                                   domo_aberto, status_domo, luz_artificial, status_luz_artificial, \
                                                   irrigacao_ativa, status_irrigacao) \
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) \
                      """
                cursor.execute(sql, (
                    dados_processados["cidade"],
                    dados_processados["bairro"],
                    dados_processados["hora"],
                    dados_processados["minuto"],
                    dados_processados["temperatura"],
                    dados_processados["status_temp"],
                    dados_processados["umidade_solo"],
                    dados_processados["status_umidade_solo"],
                    dados_processados["luminosidade"],
                    dados_processados["status_luminosidade"],
                    dados_processados["uv"],
                    dados_processados["status_uv"],
                    dados_processados["dli"],
                    dados_processados["dli_meta"],
                    dados_processados["status_dli"],
                    dados_processados["chovendo"],
                    dados_processados["domo_aberto"],
                    dados_processados["status_domo"],
                    dados_processados["luz_artificial"],
                    dados_processados["status_luz_artificial"],
                    dados_processados["irrigacao_ativa"],
                    dados_processados["status_irrigacao"]
                ))
                print("💾 Dados gravados com sucesso no banco RDS.")

    except Exception as e:
        print(f"⚠️ Erro no processamento/gravação: {e}")


if __name__ == "__main__":
    cliente_mqtt = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION1,
        client_id="IA_Servidor_Mestre"
    )
    
    cliente_mqtt.on_connect = on_connect
    cliente_mqtt.on_message = on_message

    print("🚀 Iniciando Servidor Mestre da Estufa Sirius...")
    
    cliente_mqtt.connect(MQTT_BROKER, MQTT_PORT, 60)
    cliente_mqtt.loop_forever()
