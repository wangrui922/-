import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// AI 会话日志模块
const aiSessionsRouter: Array<RouteRecordRaw> = [
	{
		path: "/ai-sessions",
		component: Layout,
		children: [
			{
				path: "",
				name: "aiSessions",
				component: () => import("@/views/ai-sessions/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "AI 会话日志",
					key: "aiSessions"
				}
			}
		]
	}
];

export default aiSessionsRouter;
