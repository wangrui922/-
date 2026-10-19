<template>
	<el-form ref="loginFormRef" :model="loginForm" :rules="loginRules" size="large">
		<el-form-item prop="phone">
			<el-input v-model="loginForm.phone" placeholder="管理员手机号" clearable>
				<template #prefix>
					<el-icon class="el-input__icon"><iphone /></el-icon>
				</template>
			</el-input>
		</el-form-item>
		<el-form-item prop="pin">
			<el-input type="password" show-password v-model="loginForm.pin" placeholder="个人密码" @keyup.enter="login(loginFormRef)">
				<template #prefix>
					<el-icon class="el-input__icon"><lock /></el-icon>
				</template>
			</el-input>
		</el-form-item>
	</el-form>
	<div class="login-btn">
		<el-button :icon="CircleClose" round @click="resetForm(loginFormRef)" size="large">重置</el-button>
		<el-button :icon="UserFilled" round @click="login(loginFormRef)" size="large" type="primary" :loading="loading">
			登录
		</el-button>
	</div>
</template>

<script setup lang="ts">
import { ref, reactive } from "vue";
import { useRouter } from "vue-router";
import { CircleClose, UserFilled } from "@element-plus/icons-vue";
import type { ElForm } from "element-plus";
import { ElMessage } from "element-plus";
import { adminLoginApi } from "@/api/modules/admin";
import { GlobalStore } from "@/store";

const globalStore = GlobalStore();

// 定义 formRef（校验规则）
type FormInstance = InstanceType<typeof ElForm>;
const loginFormRef = ref<FormInstance>();
const loginRules = reactive({
	phone: [
		{ required: true, message: "请输入管理员手机号", trigger: "blur" },
		{ pattern: /^1[3-9]\d{9}$/, message: "手机号格式不正确", trigger: "blur" }
	],
	pin: [{ required: true, message: "请输入个人密码", trigger: "blur" }]
});

// 登录表单数据
const loginForm = reactive({
	phone: "",
	pin: ""
});
const loading = ref<boolean>(false);

const router = useRouter();
// login
const login = (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	formEl.validate(async valid => {
		if (!valid) return;
		loading.value = true;
		try {
			const res: any = await adminLoginApi({
				phone: loginForm.phone,
				pin: loginForm.pin
			});
			globalStore.setToken(res.data.accessToken || res.data.access);
			const user = res.data.user;
			if (user) {
				globalStore.setUserInfo(user);
			}
			ElMessage.success("登录成功！");
			router.push("/dashboard");
		} catch (error) {
			// 错误提示已由拦截器统一处理
			console.error("登录失败", error);
		} finally {
			loading.value = false;
		}
	});
};

// resetForm
const resetForm = (formEl: FormInstance | undefined) => {
	if (!formEl) return;
	formEl.resetFields();
};
</script>

<style scoped lang="scss">
@import "../index.scss";
</style>
