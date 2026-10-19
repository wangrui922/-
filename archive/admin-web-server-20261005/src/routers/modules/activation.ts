import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 激活码管理模块
const activationRouter: Array<RouteRecordRaw> = [
	{
		path: "/activation",
		component: Layout,
		children: [
			{
				path: "",
				name: "activation",
				component: () => import("@/views/activation/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "激活码管理",
					key: "activation"
				}
			}
		]
	}
];

export default activationRouter;
