import axios, { AxiosInstance, AxiosError, AxiosRequestConfig, AxiosResponse } from "axios";
import { showFullScreenLoading, tryHideFullScreenLoading } from "@/config/serviceLoading";
import { AxiosCanceler } from "./helper/axiosCancel";
import { ResultData } from "@/api/interface";
import { ResultEnum } from "@/enums/httpEnum";
import { checkStatus } from "./helper/checkStatus";
import { ElMessage } from "element-plus";
import { GlobalStore } from "@/store";

import router from "@/routers";

const globalStore = GlobalStore();

const axiosCanceler = new AxiosCanceler();

const config = {
	// 默认地址
	baseURL: import.meta.env.VITE_API_URL as string,
	// 设置超时时间（10s）
	timeout: ResultEnum.TIMEOUT as number,
	// 跨域时候允许携带凭证
	withCredentials: true
};

class RequestHttp {
	service: AxiosInstance;
	public constructor(config: AxiosRequestConfig) {
		// 实例化axios
		this.service = axios.create(config);

		/**
		 * @description 请求拦截器
		 * 客户端发送请求 -> [请求拦截器] -> 服务器
		 * token校验(JWT) : 接受服务器返回的token,存储到vuex/本地储存当中
		 */
		this.service.interceptors.request.use(
			(config: AxiosRequestConfig) => {
				// * 将当前请求添加到 pending 中
				axiosCanceler.addPending(config);
				showFullScreenLoading();
				const token: string = globalStore.token;
				const headers = token ? { Authorization: `Bearer ${token}` } : {};
				return { ...config, headers };
			},
			(error: AxiosError) => {
				return Promise.reject(error);
			}
		);

		/**
		 * @description 响应拦截器
		 *  服务器换返回信息 -> [拦截统一处理] -> 客户端JS获取到信息
		 */
		this.service.interceptors.response.use(
			(response: AxiosResponse) => {
				const { data, config } = response;
				// * 在请求结束后，移除本次请求
				axiosCanceler.removePending(config);
				tryHideFullScreenLoading();
				// * 后端约定：成功 code == 0，失败 code != 0（HTTP 状态码与 code 同步）
				if (data.code !== 0) {
					// * 登录失效（HTTP 401 或业务码 401），清除凭证回登录页
					if (data.code === 401 || data.code === 599) {
						ElMessage.error(data.msg || "登录失效，请重新登录");
						globalStore.setToken("");
						router.replace({ path: "/login" });
						return Promise.reject(data);
					}
					ElMessage.error(data.msg || "请求失败");
					return Promise.reject(data);
				}
				// * 成功请求
				return data;
			},
			async (error: AxiosError) => {
				const { response } = error;
				tryHideFullScreenLoading();
				// 根据响应的错误状态码，做不同的处理
				if (response) {
					// 401 未登录/凭证过期：清空凭证并回登录页
					if (response.status === 401) {
						globalStore.setToken("");
						router.replace({ path: "/login" });
						ElMessage.error("登录失效，请重新登录");
						return Promise.reject(error);
					}
					return checkStatus(response.status);
				}
				// 服务器结果都没有返回(可能服务器错误可能客户端断网)，断网处理:可以跳转到断网页面
				if (!window.navigator.onLine) return router.replace({ path: "/500" });
				return Promise.reject(error);
			}
		);
	}

	// * 常用请求方法封装
	get<T>(url: string, params?: object, _object = {}): Promise<ResultData<T>> {
		return this.service.get(url, { params, ..._object });
	}
	post<T>(url: string, params?: object, _object = {}): Promise<ResultData<T>> {
		return this.service.post(url, params, _object);
	}
	put<T>(url: string, params?: object, _object = {}): Promise<ResultData<T>> {
		return this.service.put(url, params, _object);
	}
	delete<T>(url: string, params?: any, _object = {}): Promise<ResultData<T>> {
		return this.service.delete(url, { params, ..._object });
	}
}

export default new RequestHttp(config);
