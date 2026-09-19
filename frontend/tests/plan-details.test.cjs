require('./register.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');
const {create,act}=require('react-test-renderer');
const {PlanEditor}=require('../src/features/plans/PlanPanel.tsx');

test('detailed edit retains reviewed identities, metadata and inclusion criteria',()=>{
 const details={title:'Plan title',objective:'Research objective',retrieval_cutoff:'2026-09-18',
 inclusion_criteria:['Empirical findings'],subquestions:[
 {subquestion_id:'q:1',question:'First research question',priority:'critical',evidence_required:'fulltext'},
 {subquestion_id:'q:2',question:'Second research question',priority:'normal',evidence_required:'fulltext'}]};
 const plan={sub_questions:details.subquestions.map(q=>q.question),details,exclusions:[],
 source_scope:{providers:['arxiv'],year_from:2020,year_to:2026,min_papers:3,max_papers:3},
 budget_plan:{max_cny:1,max_api_calls:4,max_wall_clock_seconds:500}};
 let tree,saved;
 act(()=>{tree=create(React.createElement(PlanEditor,{plan,busy:false,onSave:v=>saved=v,onCancel:()=>{}}));});
 try {
 const question=tree.root.findAllByType('textarea').find(n=>n.props.value==='Second research question');
 act(()=>question.props.onChange({target:{value:'Revised second question'}}));
 act(()=>tree.root.findByProps({id:'plan-inclusions'}).props.onChange({target:{value:'Empirical findings\nPublic full text'}}));
 act(()=>tree.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(saved.sub_questions,['First research question','Revised second question']);
 assert.deepEqual(saved.details.subquestions.map(q=>q.question),saved.sub_questions);
 assert.deepEqual(saved.details.subquestions.map(q=>q.subquestion_id),['q:1','q:2']);
 assert.equal(saved.details.subquestions[0].priority,'critical');
 assert.equal(saved.details.retrieval_cutoff,details.retrieval_cutoff);
 assert.deepEqual(saved.details.inclusion_criteria,['Empirical findings','Public full text']);
 assert.equal(details.subquestions[1].question,'Second research question');
 } finally {act(()=>tree.unmount());}
});
