const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({language:'en'});
vm.runInContext(fs.readFileSync('github_radar/web_assets/i18n.js','utf8'),context);
const value='检索未完成，已保留上次结果：GitHub限流等待至 2026-10-07T22:15:21+08:00；已完成核算保留，可取消；检索已取消；实际读取 25 个项目；来源顺序与官方新增排名分别保存；主题首批候选，未核实全量分页；网站 AI 分类不是本软件 AI 精选；检索已取消，断点保留';
const translated=vm.runInContext('localizeServerText('+JSON.stringify(value)+')',context);
assert(!/[\u4e00-\u9fff]/.test(translated),translated);
assert(translated.includes('2026-10-07T22:15:21+08:00'));
assert.equal(vm.runInContext('localizeServerText("作者原文")',context),'作者原文');
console.log('Status translation checks passed');

for (const message of ['README 正文不可用，未调用 AI，原有解释已保留。','README 超过完整读取大小限制，未调用 AI，原有解释已保留。']) {
  assert(!/[\u4e00-\u9fff]/.test(vm.runInContext('localizeServerText('+JSON.stringify(message)+')',context)));
}
