import logging
import os

# Setup logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "routing.log")

logger = logging.getLogger("grading_router")
logger.setLevel(logging.INFO)
# Avoid adding multiple handlers if module is reloaded
if not logger.handlers:
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    logger.addHandler(fh)

def _mock_chain(chain_name, question_id, question_type, execute):
    if execute:
        raise NotImplementedError("真实分链路阅卷尚未实现")
    return {
        "question_id": question_id,
        "question_type": question_type,
        "routed_chain": chain_name,
        "status": "routed_only"
    }

def choice_recognition_chain(question_id, question_type, execute=False, **kwargs):
    return _mock_chain("choice_recognition_chain", question_id, question_type, execute)

def fill_blank_recognition_chain(question_id, question_type, execute=False, **kwargs):
    return _mock_chain("fill_blank_recognition_chain", question_id, question_type, execute)

def normal_grading_chain(question_id, question_type, execute=False, **kwargs):
    return _mock_chain("normal_grading_chain", question_id, question_type, execute)

def proof_grading_chain(question_id, question_type, execute=False, **kwargs):
    return _mock_chain("proof_grading_chain", question_id, question_type, execute)

def route_grading_task(question_type: str, exam_id: str, student_id: str, question_id: str):
    """
    根据题型分发阅卷任务到不同的链路。
    当前阶段 route_grading_task 仅用于题型识别和日志记录，不得触发模型调用。真正的分链路模型调用将在后续阶段实现。
    """
    if not question_type or str(question_type).strip() == "":
        question_type = "unknown"
        
    chain_name = "normal_grading_chain"
    chain_func = normal_grading_chain
    
    q_type_lower = str(question_type).lower()
    
    if q_type_lower == "choice":
        chain_name = "choice_recognition_chain"
        chain_func = choice_recognition_chain
    elif q_type_lower == "fill_blank":
        chain_name = "fill_blank_recognition_chain"
        chain_func = fill_blank_recognition_chain
    elif q_type_lower == "short_calculation":
        chain_name = "normal_grading_chain"
        chain_func = normal_grading_chain
    elif q_type_lower == "solution":
        chain_name = "normal_grading_chain"
        chain_func = normal_grading_chain
    elif q_type_lower == "geometry_proof":
        chain_name = "proof_grading_chain"
        chain_func = proof_grading_chain
    else:
        question_type = "unknown"
        chain_name = "normal_grading_chain"
        chain_func = normal_grading_chain
        
    log_msg = f"exam_id={exam_id}, student_id={student_id}, question_id={question_id}, question_type={question_type}, routed_chain={chain_name}, routed_only=true, model_called=false"
    logger.info(log_msg)
    
    return chain_name, chain_func
