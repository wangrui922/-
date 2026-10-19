<template>
	<div class="mastery-container">
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
			<el-table-column prop="kp_id" label="知识点ID" width="240" show-overflow-tooltip>
				<template #default="scope">{{ scope.row.kp_id || "-" }}</template>
			</el-table-column>
			<el-table-column prop="score" label="掌握度" width="140">
				<template #default="scope">
					<el-progress :percentage="Math.round((scope.row.score || 0) * 100)" :stroke-width="10" />
				</template>
			</el-table-column>
			<el-table-column prop="level" label="等级" width="100">
				<template #default="scope">
					<el-tag :type="levelTag(scope.row.level)">{{ levelText(scope.row.level) }}</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="updated_at" label="更新时间" width="180" />
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

<script setup lang="ts" name="mastery">
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { Search, Refresh } from "@element-plus/icons-vue";
import { getMasteryApi } from "@/api/modules/admin";

const loading = ref(false);
const tableData = ref([]);
const page = ref(1);
const size = ref(20);
const total = ref(0);
const searchParam = ref({ student_id: "" });

const levelText = (level: string) => {
	const map: Record<string, string> = { weak: "薄弱", medium: "中等", strong: "掌握" };
	return map[level] || level || "-";
};

const levelTag = (level: string) => {
	const map: Record<string, string> = { weak: "danger", medium: "warning", strong: "success" };
	return (map[level] || "info") as any;
};

const loadData = async () => {
	loading.value = true;
	try {
		const res: any = await getMasteryApi({
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
.mastery-container {
	padding: 20px;
}
.table-search {
	margin-bottom: 16px;
}
</style>
