<template>
	<div class="users-container">
		<div class="table-search">
			<el-form :model="searchParam" inline>
				<el-form-item label="角色">
					<el-select v-model="searchParam.role" placeholder="全部" clearable style="width: 120px">
						<el-option label="学生" value="student" />
						<el-option label="家长" value="parent" />
						<el-option label="管理员" value="admin" />
					</el-select>
				</el-form-item>
				<el-form-item label="关键词">
					<el-input v-model="searchParam.keyword" placeholder="手机号/昵称" clearable />
				</el-form-item>
				<el-form-item>
					<el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
					<el-button :icon="Refresh" @click="resetSearch">重置</el-button>
				</el-form-item>
			</el-form>
		</div>
		<el-table :data="tableData" border v-loading="loading">
			<el-table-column prop="phone" label="手机号" width="140" />
			<el-table-column prop="nickname" label="昵称" width="120" />
			<el-table-column prop="role" label="角色" width="100">
				<template #default="scope">
					<el-tag>{{ roleText(scope.row.role) }}</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="status" label="状态" width="100">
				<template #default="scope">
					<el-tag :type="scope.row.status === 'active' ? 'success' : 'danger'">{{
						scope.row.status === "active" ? "正常" : "冻结"
					}}</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="student.grade" label="年级" width="80">
				<template #default="scope">{{ scope.row.student?.grade || "-" }}</template>
			</el-table-column>
			<el-table-column prop="student.daily_target" label="每日目标" width="100">
				<template #default="scope">{{ scope.row.student?.daily_target || "-" }}</template>
			</el-table-column>
			<el-table-column prop="student.streak_days" label="连续打卡" width="100">
				<template #default="scope">{{ scope.row.student?.streak_days || "-" }}</template>
			</el-table-column>
			<el-table-column prop="last_login_at" label="最近登录" width="180" />
			<el-table-column prop="created_at" label="注册时间" width="180" />
			<el-table-column label="操作" fixed="right" width="180">
				<template #default="scope">
					<el-button type="primary" link @click="toggleStatus(scope.row)">
						{{ scope.row.status === "active" ? "冻结" : "解冻" }}
					</el-button>
				</template>
			</el-table-column>
		</el-table>
		<el-pagination
			v-model:current-page="page"
			v-model:page-size="size"
			:total="total"
			:page-sizes="[10, 20, 50]"
			layout="total, sizes, prev, pager, next"
			@size-change="loadData"
			@current-change="loadData"
			style="margin-top: 16px"
		/>
	</div>
</template>

<script setup lang="ts" name="users">
import { ref, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Search, Refresh } from "@element-plus/icons-vue";
import { getUsersApi, freezeUserApi, unfreezeUserApi } from "@/api/modules/admin";

const loading = ref(false);
const tableData = ref([]);
const page = ref(1);
const size = ref(20);
const total = ref(0);
const searchParam = ref({ role: "", keyword: "" });

const roleText = (role: string) => {
	const map: Record<string, string> = { student: "学生", parent: "家长", admin: "管理员" };
	return map[role] || role;
};

const loadData = async () => {
	loading.value = true;
	try {
		const res: any = await getUsersApi({
			page: page.value,
			size: size.value,
			role: searchParam.value.role || undefined,
			keyword: searchParam.value.keyword || undefined
		});
		const d = res.data;
		tableData.value = d.items || [];
		total.value = d.total || 0;
	} catch (e) {
		ElMessage.error("加载失败");
	} finally {
		loading.value = false;
	}
};

const resetSearch = () => {
	searchParam.value = { role: "", keyword: "" };
	page.value = 1;
	loadData();
};

const toggleStatus = async (row: any) => {
	const action = row.status === "active" ? "冻结" : "解冻";
	try {
		await ElMessageBox.confirm(`确认${action}该用户？`, "提示", { type: "warning" });
		if (row.status === "active") {
			await freezeUserApi(row.id);
		} else {
			await unfreezeUserApi(row.id);
		}
		ElMessage.success(`${action}成功`);
		loadData();
	} catch (e: any) {
		if (e !== "cancel") ElMessage.error(e.msg || "操作失败");
	}
};

onMounted(loadData);
</script>

<style scoped lang="scss">
.users-container {
	padding: 20px;
}
.table-search {
	margin-bottom: 16px;
}
</style>
