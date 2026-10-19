import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 错题管理模块
const mistakesRouter: Array<RouteRecordRaw> = [
	{
		path: "/mistakes",
		component: Layout,
		children: [
			{
				path: "",
				name: "mistakes",
				component: () => import("@/views/mistakes/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "错题管理",
					key: "mistakes"
				}
			}
		]
	}
];

export default mistakesRouter;
