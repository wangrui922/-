<template>
	<div class="mistakes-container">
		<div class="table-search">
			<el-form :model="searchParam" inline>
				<el-form-item label="学生ID">
					<el-input v-model="searchParam.student_id" placeholder="学生ID（可选）" clearable style="width: 240px" />
				</el-form-item>
				<el-form-item>
					<el-button type="primary" :icon="Search" @click="loadData">搜索</el-button>
					<el-button :icon="Refresh" @click="resetSearch">重置</el-button>
				</el-form-item>
			</el-form>
		</div>
		<el-table :data="tableData" border v-loading="loading">
			<el-table-column prop="student_id" label="学生ID" width="240" show-overflow-tooltip />
			<el-table-column prop="question_id" label="题目ID" width="240" show-overflow-tooltip>
				<template #default="scope">{{ scope.row.question_id || "-" }}</template>
			</el-table-column>
			<el-table-column prop="error_type" label="错误类型" width="120">
				<template #default="scope">{{ scope.row.error_type || "-" }}</template>
			</el-table-column>
			<el-table-column prop="status" label="状态" width="110">
				<template #default="scope">
					<el-tag :type="scope.row.status === 'solved' ? 'success' : scope.row.status === 'reviewing' ? 'warning' : 'info'">
						{{ statusText(scope.row.status) }}
					</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="created_at" label="创建时间" width="180" />
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

<script setup lang="ts" name="mistakes">
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { Search, Refresh } from "@element-plus/icons-vue";
import { getMistakesApi } from "@/api/modules/admin";

const loading = ref(false);
const tableData = ref([]);
const page = ref(1);
const size = ref(20);
const total = ref(0);
const searchParam = ref({ student_id: "" });

const statusText = (status: string) => {
	const map: Record<string, string> = { solved: "已解决", reviewing: "复习中", pending: "待处理" };
	return map[status] || status || "-";
};

const loadData = async () => {
	loading.value = true;
	try {
		const res: any = await getMistakesApi({
			page: page.value,
			size: size.value,
			student_id: searchParam.value.student_id || undefined
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
	searchParam.value = { student_id: "" };
	page.value = 1;
	loadData();
};

onMounted(loadData);
</script>

<style scoped lang="scss">
.mistakes-container {
	padding: 20px;
}
.table-search {
	margin-bottom: 16px;
}
</style>
