import { createApp } from 'vue'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import App from './App.vue'
import './style.css'

// 界面全是中文，dayjs 的「周几 / 相对时间」也得是中文
dayjs.locale('zh-cn')

createApp(App).use(Antd).mount('#app')
