import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 掌握度模块
const masteryRouter: Array<RouteRecordRaw> = [
	{
		path: "/mastery",
		component: Layout,
		children: [
			{
				path: "",
				name: "mastery",
				component: () => import("@/views/mastery/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "掌握度",
					key: "mastery"
				}
			}
		]
	}
];

export default masteryRouter;
