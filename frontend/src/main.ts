import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/theme-chalk/base.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-input.css'

import App from './App.vue'
import { createAppRouter } from './router'
import './styles/tokens.css'
import './styles/element-theme.css'
import './styles/base.css'
import './styles/app-shell.css'
import './styles/review-queue.css'
import './styles/review-evidence.css'
import './styles/review-scoring.css'
import './styles/workbench.css'
import './styles/session-config.css'

const app = createApp(App)
const pinia = createPinia()
const router = createAppRouter()

app.use(pinia)
app.use(router)
app.mount('#app')
