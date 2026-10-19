import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 系统配置模块
const configRouter: Array<RouteRecordRaw> = [
	{
		path: "/config",
		component: Layout,
		children: [
			{
				path: "",
				name: "config",
				component: () => import("@/views/config/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "系统配置",
					key: "config"
				}
			}
		]
	}
];

export default configRouter;
