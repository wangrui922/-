<template>
	<div class="login-container flx-center">
		<SwitchDark class="dark"></SwitchDark>
		<div class="login-content">
			<div class="login-left">
				<img src="@/assets/images/login_left0.png" alt="login" />
			</div>
			<div class="login-box">
				<img class="login-img" src="@/assets/images/form_icon.png" alt="form_icon" />
				<div class="login-form">
					<div class="login-logo">
						<img class="login-icon" src="@/assets/images/logo.svg" alt="" />
						<p class="logo-text">错题教练 · 管理后台</p>
					</div>
					<LoginForm></LoginForm>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup lang="ts" name="login">
import SwitchDark from "@/components/SwitchDark/index.vue";
import LoginForm from "./components/LoginForm.vue";
</script>

<style scoped lang="scss">
@import "./index.scss";
</style>
