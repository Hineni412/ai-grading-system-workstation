import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { createAppRouter } from './router'
import './styles/tokens.css'
import './styles/base.css'
import './styles/app-shell.css'
import './styles/file-center.css'
import './styles/results-center.css'
import './styles/ui-effects.css'
import './styles/tailwind.css'

const app = createApp(App)
const pinia = createPinia()
const router = createAppRouter()

app.use(pinia)
app.use(router)
app.mount('#app')
