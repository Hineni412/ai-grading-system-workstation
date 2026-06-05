-- 记录生成题库 AI 标签的模型名称，只保存模型名，不保存接口地址或请求日志。
ALTER TABLE question_tags ADD COLUMN model_name TEXT DEFAULT NULL;
