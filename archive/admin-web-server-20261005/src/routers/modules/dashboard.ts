import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 数据看板模块
const dashboardRouter: Array<RouteRecordRaw> = [
	{
		path: "/dashboard",
		component: Layout,
		children: [
			{
				path: "",
				name: "dashboard",
				component: () => import("@/views/dashboard/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "数据看板",
					key: "dashboard"
				}
			}
		]
	}
];

export default dashboardRouter;
