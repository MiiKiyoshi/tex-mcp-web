# Neutral Paper: Content Guideline

This guideline is for a paper written in LaTeX. Its author edits the .tex and .bib sources, and its reader reads the compiled PDF. Article 6 holds the paper's section order, its abstract, Introduction, and paragraphs, and the warnings its compile reports. A domain guideline that extends this one takes precedence where the two differ.

## Article 1. Priorities and shared terms

1.1 **Judge a paper by the reader's understanding.** A paper succeeds when it brings its reader to the requested understanding or decision with grounded claims and values within authorized scope. The reader following each section's argument ranks above rigor and completeness.

1.2 **Headings, abstract, and captions are adaptable defaults.** Section and subsection headings, the abstract, and captions are defaults unless explicitly required. Adapt any default that obscures meaning, repeats content, or displaces needed explanation. Marked pending placeholders are exempt from density and evidence requirements.

1.3 **Each step follows from established premises.** An argument is followed only through a chain in which each claim or step follows from definitions and premises established before it in the paper or in the reader's knowledge.

1.4 **Include only what the reader needs.** Put in the paper only what the reader needs to follow its argument, apart from required attribution, and keep other detail in the planning records.

Before adding an element, or retaining one whose supporting context has changed, identify the information, condition, distinction, or guidance it gives that the reader needs there to follow the argument. Count what the reader's knowledge and existing words, figures, and conventions already give. If omission loses nothing needed, omit the element. Each detail inside an element, such as a label, number, unit, qualifier, or note, is an element of its own and passes this test by itself, not with the element that holds it. An element also fails when it answers an objection neither the requester nor the reader has raised, or when the reader has no reference by which to read it. Apply this test at every scale, and judge necessity separately from clarity or fit. Within a sentence or label, a word or ending that marks how other words relate gives that relation, so omit it only when those words already give the relation as the reader reads them.

1.5 **Treat only requester decisions as binding.** A requester decision is a requirement or approval given explicitly by the requester or by a reviewer to whom the requester delegated that authority. It binds within its scope until replaced or withdrawn, and code, logs, earlier drafts and reports, and handoff notes do not reinterpret it. Everything else, including plans, section structure, layouts, and reviewed drafts other than an approved skeleton (2.11), is a revisable author choice. Reuse approved wording, tables, and figures that express the required content, and preserve wording specified as exact. A sentence the requester has said reads well is approved wording.

The editing scope is the sections and kinds of change the requester has asked for or allowed. Edit within it and within placement constraints, and ask before exceeding it or an explicit page limit. New measurements and their criteria are the requester's to choose: report a doubted claim and what could test it, and change no claim because of a measurement the requester did not ask for.

1.6 **Report conflicts, never silently resolve them.** Report a conflict between a requester decision and a rule, current evidence, or another active requirement, without silently changing the decision, suppressing the observation, or dropping the requirement.

1.7 **Recommended conventions give way to the venue and the requester.** A convention this guideline calls recommended is the field's usual practice. Follow it unless the venue's format or a requester decision sets another, and then follow that.

## Article 2. Plan the argument

2.1 **Derive questions from the request, not sources.** Before planning, establish purpose and intended use, the reader's starting knowledge, and the understanding or decision they require. Derive the questions the paper answers, their content, and its level of detail from the current request and discussion, not from sources, available material, or the slots of an existing structure, and do not substitute an easier, documented, or measurable question for the one requested.

Work backward to prerequisites, then order questions forward from what the reader knows, reusing established answers that later questions depend on. Record the starting knowledge and, for each required question, the answer the reader should retain, its prerequisites, evidence, form, and successor. Record a gap that evidence cannot yet fill as unresolved.

2.2 **Take each fact from its authoritative source.** Before collecting content, identify authoritative sources for direction and terminology, and inspect requested references. Take intended design from authoritative design descriptions, construction from implementation sources, and what happened from primary measurements or execution records. Use secondary or additional sources only to fill a needed missing, inconsistent, or changing fact, checking current sources for changing facts. For a claim that one part produces an effect, trace the connection from cause to effect in the implementation or records. Verify that commands, files, paths, and implementations presented as existing do exist.

When the paper restates a plan, proposal, earlier report, or earlier paper, check each of its claims and citations against the primary source, and write what the source says, including where the source corrects what the earlier text said of it.

2.3 **Cite primary sources and credit borrowed material.** Cite the claim's primary source, not a summary or repository merely carrying it, although a repository can be primary evidence for implementation. Include details needed to identify and interpret evidence. Credit borrowed material, mark adaptations, and attribute work to its actor. When a figure in a published source shows what the paper needs and its notation, labels, and label language agree with the paper's under 4.1, 4.4, and 4.6, reuse it, citing its location in the source in the reference entry. Otherwise redraw it in the paper's notation and mark it adapted.

2.4 **Derive every reported value from primary data.** Check primary evidence that a reported result exists and is final, and mark pending results or omit the claims that depend on them. Generate results and derived numbers from primary data before placing them in figures or tables, and compare reported values with that output. Identify each quantity as measured, source-reported, assumed, chosen, or computed, showing its source, decision record, or derivation, and verify chosen settings against their configuration or decision record.

Record the inputs, rules, and assumptions needed to recompute values. Compute a value over everything its rule covers, including the cases hardest to handle. For counts, identify the items and how groups form the total.

Before calculating an example, establish the validity of its inputs and rule and limit the conclusion accordingly. Label illustrative and assumed values and check their calculations separately. An example does not establish a measured result. When a method or setting changes how results are produced, do not apply it to results produced without it, even as an illustration.

2.5 **Build and trace the chain before drafting.** Build the chain of 1.3 before drafting. Before introducing a concept, identify the concrete problem that makes it needed, and connect a method's change to its intended effect on that problem.

Trace the mechanism or calculation steps the argument needs through primary sources, from inputs, operations, and conditions through outputs and their later use to the required result. Record each step's primary source location. For each value or claim the argument rests on, record the definitions and premises it follows from, marking which the reader already knows. Check simplifications against that trace, and mark unresolved steps as unverified in the plan and in dependent drafts.

2.6 **Derive sections from the required argument.** Derive the sections and subsections from the required argument under Article 6. Existing headings and proposed lengths do not constrain them unless the requester fixed them. Order the questions and answers with their prerequisites and evidence, and group steps by causal chain, comparison, mechanism, or decision. Each subsection answers one question with evidence in a readable form chosen under 5.1 and 5.2.

Record this sequence and grouping before drafting, as the section map, showing each section and subsection with the others at its level.

If the needed explanation exceeds a page limit, reduce the scope within authorized constraints. Do not meet a length by inventing terms, obscuring relationships, or omitting needed explanation. When removing content, remove what fails 1.4 or repeats a claim. Keep the prerequisites, definitions, mechanism or calculation steps, conditions, evidence, local context, and details the reader needs to follow each answer and its reasoning.

Spend the page budget on the paper's own contribution. Explain the method's components in detail. Keeping the Introduction and Background together to about a third of the page limit is recommended (1.7), and when they exceed it, shorten or drop first the background subsections the method's explanation needs least. When a subsection's components each need more than one paragraph, give each component its own subsubsection.

2.7 **Order sections by prerequisites.** Order sections by prerequisites under 6.1. Distinguish background from the subject by explanatory role, not source or novelty, and make headings match. For empirical work, present method, setup, then results.

In the method, definitions that only the procedure uses, such as its intermediate states and transitions, go with the procedure. In the experiments, after the setup, the recommended order (1.7) is the accuracy of any model or estimator the method relies on, then the comparison with baselines, the ablation, a case study, and runtime.

2.8 **Keep planning records in one file.** Keep the planning records in one file outside the paper's LaTeX sources, in the paper's language, recording choices as they are made. The file holds:

1. the starting knowledge and the question records of 2.1,
2. the value derivations of 2.4,
3. the source locations and chain trace of 2.5,
4. the section map of 2.6,
5. a term table giving each unfamiliar term's definition and the section where it is first defined,
6. revision history, and each decision with its authority, scope, reason, and status as active, replaced, or withdrawn, recording the author's implementation of a requester's instruction as an author choice,
7. the answers the compiled sections leave under step 1 of 7.2, and the missing idea a repeated confusion report names under 8.3.

2.9 **Revise the plan from the required outcome.** The plan is provisional. When research or drafting reveals missing explanation or contradicts planned claims:

1. Return to the required outcome and reassess which claims matter to it.
2. Revise or withdraw unsupported author claims.
3. Select replacement content by that outcome and its evidence.
4. Update the affected records, then the needed prerequisites, steps, conditions, and evidence.
5. Reassess the section map before placing content.

2.10 **Keep each section to its role.** A section holds only the content of its role: background the method needs in the background, the method's rules, steps, inputs, and outputs in the method, the conditions, settings, and benchmarks in the setup, and measured values with their interpretation in the results. Outside the results, show no measured value, result, or instance taken from a particular run, under 5.6. Where such a section must show the form of an output or record, read several real ones and show the form they share, with placeholders for their values and names.

State the method in the field's terms: what each part takes, does, and produces. Generalize or omit code-level detail, such as the programming language, API and function names, diffs, shell tools, log syntax, and file paths, keeping a tool command or option name only where 4.1 needs it to identify the subject. A property that changes how the method behaves is not code-level detail: keep it and state in one clause what it gives. A comparison against a baseline belongs in the comparison, not the ablation, even when it uses an ablation condition. A case study shows the process of one run in a few marked scenes, and no value a table already shows.

2.11 **Keep a skeleton's notes in the sources.** When the requester drafts from a skeleton, write it as comments in the LaTeX sources, one block per paragraph headed by the paragraph's topic, one claim per line, in the requester's language, with each cited work's full reference under the line that uses it. A drafted paragraph keeps its notes above its prose. Notes name sections, figures, tables, and equations by their printed numbers and titles, since a comment cannot use \ref, and are updated when those numbers change. They state what a thing is and does rather than what it is not, and carry no implementation reason or drafting history. Draft the prose in the paper's language from the notes' content under 4.1, not by translating them sentence by sentence. While notes carry the content, the reviewer reads the sources: judge first use under 4.3 and order over the notes and the printed text together, in source order. Once prose replaces the notes, judge them on the compiled paper. A skeleton the requester has approved binds as a requester decision: draft the prose from its notes in their order, and do not reword, move, or retitle a note to fit the prose. When another rule of this guideline asks for content or an order the notes do not give, follow the notes and report the difference (1.6).

## Article 3. Draft sections

3.1 **Draft in reading order, not source order.** Draft in reading order, applying 1.4 to each paragraph, figure, table, and equation and establishing missing prerequisites and relationships before dependent content. This order, not source order, determines what comes next.

1. From the reader's knowledge and preceding sections, state the next question, the answer to establish, and the chain it rests on with a method's need and intended effect. Select evidence the reader can check.
2. Name each object or action and place needed definitions before use. Construct the answer's relationship under Article 5 with the needed objects, operations, conditions, and evidence.
3. Write the paragraphs that carry the argument, referencing each figure and table where the text needs it.
4. Head the section and subsection under 4.7.

3.2 **Claim only what shown evidence supports.** Before writing a claim, distinguish fact, definition, observation, inference, assumption, proposal, decision, and unknown, provisional values from final ones, and external examples and proposed components from existing work. Establish the cause of an observed result with evidence that distinguishes it from plausible alternatives, and name the source. Otherwise state the observation and label the explanation as proposed.

State tested cases and the conditions under which a claim holds, in the sentence that makes it, such as the evaluation script or data on which it was measured. If only part of the cases introduced was tested or shown, explain why and word the conclusion to cover only that part. Limit every claim through what it asserts to what its shown evidence supports, not through added qualifiers or caveats. Source background facts and support claimed benefits separately from chosen settings. Omit unsupported claims or mark them unresolved. Cut self-evaluation, inflated claims, and process narration.

State a problem and why it is one. Do not replace it with an evasive phrase that names no consequence, such as saying that something becomes a separate issue.

Ground relationships implied by conceptual drawings, including ordering, crossings, and meaningful relative size, in sources or checks under stated conditions. Omit unsupported relationships.

State a limitation of the proposed method only together with an alternative under the same constraints that would avoid or reduce it. Without one, the effect is a condition of the problem, not a limitation: omit it, or state it as a condition where a claim's scope depends on it.

3.3 **Show each step's actor, inputs, and outputs.** For a process or comparison, show:

1. each step's actor, inputs, and outputs, and how its output determines the next step,
2. the conditions and actions for branches, repeats, stops, and rejected inputs,
3. inputs, transformations, and outputs kept together, and changed and fixed variables kept together,
4. for multiple actors, each one's role, its inputs, what it decides, and its outputs, what it may change, and the resulting states,
5. for a reduction or other change to objects or states, the affected range and what is retained, extended, replaced, or discarded.

3.4 **Explain objects where they first raise questions.** Explain an object or relationship where it first raises a question, or just before. Show outputs where produced and uses where consumed. Refer to figures, tables, equations, and sections by their numbers, written with \ref or \cref on their \label, and to sources with \cite, never with a number typed into the text. Repeat prerequisites as local context only to avoid looking elsewhere or recalling hidden detail, without proving them again. Refer forward only to locate the answer to a deliberately open question.

3.5 **Establish each claim only once.** Establish each claim once. Where a claim recurs beyond such local context (3.4), add a consequence or interpretation. Expand an overview by answering a further question, not repeating the topic. Combine subjects only through a real connection. Resolve or report contradictions with earlier sections.

Define each term and rule in the method, and give in the setup only the conditions, settings, tools, and benchmarks with their values. Do not restate an equation in prose. Before writing a term, find where the paper defines it and refer to that definition.

3.6 **Show the mechanism before naming it.** For an unfamiliar mechanism, show what happens to named objects, what changes, what stays fixed, and what follows before attaching its name and definition. Establish its referent and defining rule at the section's level, then build the formal explanation on those relationships. Accept introductory simplifications that preserve the relationships and agree with the formal explanation and with grounded drawings (3.2).

Introduce an unfamiliar general concept through an instance of it familiar outside the work, the one commonly used to teach it, such as a table of house prices for tabular data, then state in one line that the work's setting has the same form.

To justify a model over a simpler one, show what the simpler model misses, why it matters, and how the chosen model accounts for it, comparing under conditions that isolate that difference.

3.7 **Justify a design choice by a widely accepted finding.** Justify a design choice by a widely accepted finding, cited, and by showing that the setting meets the conditions the finding is about. Add no measurement, count, or criterion of the author's own to pre-empt an objection the reader has not raised, and no number the reader has no reference to judge.

## Article 4. Words and notation

4.1 **Use established field terms, never coinages.** Think and draft each text in its reader's language rather than translating it. Name each object or action as the reader and field name it in the paper's language, including established loanwords, with expressions of clear meaning and actual common use, in headings, captions, and labels too. Where the field uses an English term, write the English term, such as baseline, backbone, adapter, retention, or word problem, not a translation of it. Do not form names by literally translating internal code names or foreign words, or replace established terms with paraphrases or coinages to sound plainer. Avoid rarely used words and word pairings, and common words in a part of speech unusual for them, even when their dictionary meanings fit.

Where no established term fits, describe the relevant objects, properties, and relationships. Do not substitute unexplained identifiers, working labels, or type signatures for explanation. Retain command, option, and data structure names needed to identify the subject.

In Korean, put a count before its noun, in digits under 4.5, or name the members, not a noun followed by a count and a particle: 3개 model, or the members' names, not model 셋은. Replace vague nouns such as 양, 것, and 측면 with the name of the quantity or object.

4.2 **Name the operation, not a person's act.** Describe an object, program, model, agent's output, or quantity by what it computes, holds, or outputs, never with a verb for what a person does by choice or mind, such as knowing, expecting, wanting, trying, judging, following, taking on, keeping, correcting, or stepping, unless that verb is the field's technical term for the operation, such as a model that predicts or a router that routes: a tree whose output is the value of the leaf a sample falls in, not a tree that follows the remaining error, and a model that gives an answer a high probability, not one that knows the answer. A system that chooses its own actions is exempt from this: write it, as a person, as the actor of what it chose and did and of the evidence it used. The test is the meaning, so it holds for the verbs and endings of whatever language the paper is written in, and for a title or label phrased as a question, which puts a person's deliberation in a thing's place. A requester's informal explanation gives the idea, not the wording, so restate it as what is computed. Name what passes between parts of a system as one part's output and another's input, not with verbs of a person's exchange, such as receiving, submitting, or reporting.

When describing an action or change, first state what acts or changes and what happens to the affected object, with no long list or long clause between the action and its participants, and keep the action and participants needed to identify it in shorter headings, labels, definitions, and captions. Name the operation itself, such as multiplying an output by a factor below one, not a vague verb for its effect, such as weakening it, and name an object, not a figure of speech for it. Name steps by object and operation at the level explained, not a hidden substep.

Describe special cases by conditions and effects, and state when a claim holds, not the author's act of assuming it. Do not invent a domain explanation for an implementation condition without evidence connecting them. Join statements with an explicit conjunction when their relationship matters, a contrast or turn included.

When a sentence states a cost or trade-off, name what grows, such as computation, latency, or parameters, and use the field's term for the result, such as inference cost, from its first use.

Before reusing a heading, definition, label, or caption, read the whole clause with its figure to identify participants, action or state, and relation, and rewrite unclear clauses and connected uses, not only the flagged word.

4.3 **Define unfamiliar terms at first use.** Define unfamiliar terms and symbols, including ordinary words used technically, at first use by their kind and what they represent or do, in established vocabulary. The abstract is read first and on its own: it uses no internal name or symbol that it does not define. For a quantity, first identify what it counts or measures, using an established term when one fits and keeping the object and its interval or unit together. Put needed conditions and how the value is obtained afterwards.

Do not replace or interrupt that meaning with its equation role, another symbol's role, or a demonstrative referring to another symbol. A drawing's appearance or parts do not define it. Leave structure and operation to the figure or equation rather than retelling them in the definition. A specific index of an established symbol is a later use if its meaning is unchanged.

At first use, expand an abbreviation unfamiliar to the reader or confusable within the paper as full name followed by the abbreviation in parentheses, including in headings, captions, and tables. Leave routine field abbreviations unexpanded regardless of source. Afterwards use the abbreviation alone unless local clarity requires the full name.

4.4 **One term and notation per meaning.** Use one term and visual notation per meaning. Preserve qualified names when the kind matters or the requester specifies them. Keep distinct names or indices where differences matter, including later uses.

4.5 **Identify quantities by name, counts by digits.** Write counts as digits followed by their unit or counter, unless a shared label or header identifies what is counted. Keep each assignment of a value to a symbol visually separate from the next.

In text, identify quantities and drawn elements by established names or their defining wording, using appearance and position only to locate them. When an equation's form is the subject, name mathematical terms and operations directly. Explain the relation a quantity describes, including needed direction or endpoints.

4.6 **Set all math notation in LaTeX math mode.** Set all mathematical notation in math mode, in text, tables, and figures, including labels set in TikZ or written by a plotting script. This covers everything from a single variable or operator in a label to a whole formula or a numeric calculation with its units, including every subscript and superscript. Write each symbol everywhere, including headings and captions, in the form its definition gives, never a simplified variant, and where the paper defines its symbols as macros, define each one once and write it through its macro. Reserve mathematical symbols for mathematics. Write words inside mathematical expressions in English, keeping surrounding explanations in the paper's language.

Use one equation per definition step, numbered and labeled when the text refers to it. Give each symbol one role and distinguish assumed from solved values.

4.7 **Headings are noun phrases naming subjects.** Head sections and subsections with noun phrases naming the subject at the level explained, such as Proxy Dev Sets or Interference-Aware Rank Allocation, not its conclusion, incidental source, or an action used merely to obtain it. Do not use a bare generic word, such as Approach or Plan, as a subsection heading. English headings in Title Case are recommended (1.7), in a paper written in another language too.

4.8 **Punctuate without semicolons or clauses in parentheses.** Write no semicolon in prose, captions, table cells, notes, or replies. Parentheses hold a noun or symbol list, a value, or a pointer such as a table or equation number, never a definition, condition, or reason, which becomes its own sentence.

## Article 5. Figures and tables

5.1 **Plan figures before prose for relationships.** Plan figures before prose for mechanisms, arrangements, sequences, branches, merges, loops, comparisons, and changes. Draw essential relationships, not just objects. Use an equation or a paragraph when a figure adds nothing. Draw a figure in TikZ, or write it as a vector PDF from a plotting script that reads the primary data under 2.4. Set a figure at the column's width when it reads there, and across both columns of a two-column layout, as figure*, only when its content needs that width. If a needed relationship cannot yet be drawn, report what is missing and continue independent sections.

5.2 **Match the figure form to the relationship.** Match the form to the relationship.

| Relationship | Form |
| --- | --- |
| Sequence | Directed path |
| One input under conditions | Branch |
| Conditions entering one evaluation | Convergence |
| Compared alternatives | Aligned inputs, stages, and outputs on shared axes |
| Hierarchy | Tree |
| State change | Before, action, after |
| Exact comparison | Table or shared-axis chart |
| Independent peers | List or table |

Draw each operation at the objects it acts on, not apart from them. Label relations not explained by visual convention. Before rejecting an approach because its current drawing falls short, try another form or arrangement that keeps the needed content.

5.3 **Lay figures out in reading order.** Default to left to right and top to bottom. Complete independent columns of a figure in reading order and align matching items across comparisons. Put outputs after their producing steps even if reversing that order would shorten a connector.

5.4 **Choose connectors from the represented relationship.** Choose connector endpoints, entries, exits, labels, and direction from the represented relationship before routing. For processes read left to right and top to bottom, prefer top or left inputs and bottom or right outputs when sides make no visible difference. Use another side for a visibly simpler route preserving direction, junctions, and reading order. These defaults exclude feedback, return lines, and connections to symbols whose connection points are fixed by convention. Merge only when direction and junctions show which sources reach which targets.

5.5 **Give each labeled part its own panel.** Choose pictograms with established meanings for object kinds and roles in conceptual explanations unless standard symbols or the drawing already convey them.

Enclose separately labeled diagram parts and stages in separate panels with labels and explanations inside. Put each part or stage label at its block's top left, as a noun phrase naming the part, not a question. Only parts compared on shared axes may share a panel. Center a label or formula on the shape, connector, or junction it names, in both directions. Extend or rearrange drawings for additions instead of squeezing them into gaps.

Write an explanation under a drawn object as one chain from the drawing: first the property of the object the drawing makes visible, then the effect that property causes, named by the field's term. Under a tree drawn with many small leaves, write that each leaf holds few samples, so the tree overfits noise, not that the tree chases chance errors.

Size content for readability and required relationships. Do not add content or increase its size merely to match panel shapes or fill unused space.

5.6 **Explain with the smallest hand-checkable instances.** Explain mechanisms with the smallest set of instances the reader can check by hand, chosen to distinguish the stated rule from a baseline or plausible shortcut when the argument requires that distinction. Give quantities the rule keeps distinct different values in the instance, so that no step reads as a shortcut that equates them: a fitted output that differs from the target it was fitted to, not one equal to it. For general rules, default to generic examples with short placeholder names and simple values. Use real names or measured values when identity or magnitude matters to the claim.

Show purpose, operation, and conditions and scope beyond the instance, including an input and its resulting output for a claim about how a system responds. When connecting concepts to code, formats, or records, keep source excerpts limited to the argument's needs and show their correspondence.

5.7 **Draw unchanged structure once.** Draw unchanged structure once with changing values unless understanding, comparison, counting, or a later question requires repeated instances. When one procedure repeats per item or per combination of conditions, group each repetition with its inputs and results. Show the direction of any scan, traversal, or variation drawn. Mark abbreviated depictions and give their total. Redraw changed structures with both states and their difference. Show full curves only when shape matters.

5.8 **Every figure is a labeled, referenced, captioned float.** Every figure and table is a float with a caption and a \label, set at the top of a column or page, and is referenced in the text where the argument needs it, before the float appears. Label parts of a figure with (a), (b), (c) in reading order. Give labels, numbers, and captions visible referents, name each plot axis by its quantity and unit, and identify compared cases. Explain visual distinctions through the drawing, a convention, or a legend with actual mark or line samples beside their meanings. Repair unclear relationships and misleading notation in the drawing, not in the caption.

When a table or plot first shows an unfamiliar computed quantity, define it before the visual by an equation in the paper's notation, writing a constant taken from data or a setting as a defined symbol.

A caption opens with a short line naming what is shown, and may add up to two sentences that tell how to read it, such as its stages or the conditions of its values. A format shared by several tables is described once in the text, and their captions refer to it as the form of the first such table. The interpretation of a figure or table, and any worked calculation with its values, go in the paragraph that references it.

5.9 **Table cells hold labels, numbers, or marks.** Use rows for entities or conditions unless transposing clarifies comparison. Rows and columns must belong to their header's set at the same grouping level. A cell holds a short label, a number, or a mark, never a sentence: move explanation into the text. Put conclusions and facts of another kind outside the table. Aggregates computed from member rows or columns may remain if labeled and set apart. Order entries meaningfully, state units once, identify sources of differing measurements, and keep group conditions and needed explanation with results.

When a column repeats one value in every row, state the value once outside the columns and drop the column. Keep the column when that value is a measured number or when the column is one of several compared columns and another of them varies.

A list of names, such as datasets or models with their sizes or roles, goes in a table, not in a sentence.

Apart from such labeled aggregates, no column holds a value the reader can compute from the other columns of its row, and a reported total stands without its breakdown unless the breakdown is the claim. Show a result against its reference: give the reference values, and each method's value with its change as a percentage in parentheses after it, as in 80 (+25%), never as a number of its own, with a positive change meaning an improvement. Keep a row per benchmark instance, each cell the mean over runs with its spread and change, and let an aggregate row cover only the instances every column has.

List the quantities the paper defines side by side for one object, such as a model's input features or a method's parameters, in a table, one scalar per row, with three columns: the indexed symbol, an English name, and the definition with its unit. For input features these are headed Input, Feature, and Value, and a diagram of input vector, model, and output goes beside the table. Give each object one symbol, list quantities shared by several objects once, and do not paraphrase such quantities in prose or merge several into one row.

5.10 **Reuse the existing figures' visual style.** When extending or restyling a paper, reuse the palette, line and arrow styles, typography, and panel label placement of its existing figures, use existing visual distinctions before adding new ones, and preserve their meanings when adapting figures.

5.11 **Show a plotted quantity's full range.** For a plotted quantity with a defined range, show that full range when claiming the value's size, adding a reference line when a stated threshold matters. Crop to show differences between close values when those differences are the claim, and visibly mark the cropped range.

## Article 6. The paper

6.1 **Follow the usual section order.** A paper, a proposal included, begins with its Introduction rather than with the headings of a funding or research-plan form. The recommended order (1.7) of its sections is, for example:

1. Introduction,
2. Background, with related work as its subsection,
3. Proposed Methodology,
4. Experimental Results, opening with an Experimental Setup subsection, or Experimental Plan when no results exist yet,
5. Conclusion.

Leave out a section the argument does not need, and name a section added for a need the argument has under 4.7.

6.2 **Abstract states problem, method, and result.** The abstract states the problem, the method, and the result or expected outcome in one paragraph, under 4.3, with no figure. A figure of the work's main structure belongs in the Introduction.

The abstract names the surrounding work in one clause, without citations or system names. It states each limitation of prior work that the method addresses in its own sentence, and what the method does that prior work does not, gives the method's overall flow in one sentence, and ends with the result and its numbers. It compares against a general baseline or one representative prior work, or reports metrics alone, never against a list of specific configurations. When the submission system limits the abstract's words, keep a version within the limit as a comment in the abstract's source.

6.3 **The Introduction moves from general use to the problem and the method.** The Introduction gives the general use of the paper's technique in one paragraph, without limitations when that use lies outside the paper's problem. The next paragraph states the problem through the direction of the work closest to it and that work's limits at a general level, leaving each work's detail to the Background. Then it states the method with one sentence per component, and ends with a short list of contributions, three or four as recommended (1.7), that names baselines by their feature, not their name, and lists no ablation or validation. The Conclusion names baselines as the Introduction does. The contribution list holds no list of tasks or hypotheses: detail of hypotheses and their tests belongs in the experimental sections.

6.4 **Each paragraph advances one line of thought.** Write each paragraph as one line of thought that every sentence advances. A Korean paper uses the plain written style, ending its sentences in -다, not the polite style.

6.5 **Clear compile warnings by rewriting or fixing the source.** After each compile(), read the warnings it reports in its log: overfull and underfull boxes, undefined references, and undefined citations. They are the author's to clear. Clear a box in running text by rewriting the sentences around it, with no meaning lost: cut redundancy, reorder, or choose another established word. Clear a box that a table, figure, or equation causes by changing its source until it fits its column. Clear an undefined reference or citation by fixing the source: the \label, the key in \ref, \cref, or \cite, or the .bib entry it names. Do not suppress a warning, such as with \sloppy, a raised \hfuzz or \hbadness, or a forced line break. Leave approved wording (1.5) unchanged, and report a warning that only a change to it would clear.

6.6 **Related work takes one sentence per work.** Group works into paragraphs by approach. State a limitation shared by a paragraph's works once at its end, and a work's own limitation in one more sentence right after that work. Describe compared work in a neutral tone. Omit calculations the field already knows unless the method changes them. When a section presents a few representative methods of a space, state why they cover it.

## Article 7. Check the compiled paper

7.1 **Read every affected compiled page.** After drafting a new paper or for a review of the whole paper, compile it and read every page of the PDF with image(). After changing content or its underlying system, configuration, or data, compile and read the pages of every affected section, including unedited dependents and other explanations of the same point. After deletions or moves, relocate needed prerequisites and update dependents.

7.2 **Check the chain before form and layout.** Check in this order. Before the next step, repair what steps 1 to 3 find under 8.3, and repair what steps 4 and 5 find directly.

1. Chain. Read each section on the compiled pages as its reader, in reading order, using only the reader knowledge recorded under 2.1 and 2.5 and earlier sections. Write the answer it leaves beside its planned answer in the planning records. Check that each value or claim the answer rests on follows from those alone. The author's knowledge, records, and sources, and the paper's LaTeX source, make missing steps look present, so consult them only to fill a gap the compiled paper shows.
2. Outcome. Check that these answers establish the required outcome, connecting the needed problem, premises, method, and effect.
3. Structure. Find content outside its section's role under 2.10. Find answers that differ from the plan, missing connections, relations 5.1 calls for drawing that are only stated, and elements that fail 1.4, asking of each label, number, unit, and note what it gives that the names, figures, and earlier content do not. Check the section order and headings against Article 6.
4. Form. Read the verb of every sentence, title, and label whose subject is not a person, and rewrite each that 4.2 forbids. Check compiled content against Articles 2 to 6, including 4.5, 4.6, and figures and tables under 5.8 and 5.9. Compare it with requested references.
5. Layout. Read the warnings of the last compile() and clear them under 6.5.

7.3 **Passing checks do not prove understandability.** A successful compile, preserved edits, a log without warnings, or an independent review's pass does not establish an understandable argument.

## Article 8. Respond to a comment or review request

8.1 **Read all related comments before responding.** Before responding:

1. Read the comments that bear on the same request and the material they point to, and decide from all of them together what each refers to.
2. For a supplied rewrite, identify the failure it corrects and apply the underlying requirement to other affected passages.
3. Gather the section's purpose and active requirements, including those carried with moved content. Find requester decisions in planning records, % comments in the .tex and .bib files and in any script that generates a figure or table, and relevant commit messages.
4. After moves or removals, read connected instructions together, preserving distinctions and excluding rejected content.
5. If materially different readings remain, ask one focused question.

8.2 **A question about meaning reports confusion.** A question about what any part of the paper means also reports that it confuses its reader, and a thread answer does not repair the paper. Within the editing scope, repair the paper under 8.3. Outside it, answer without editing, reassess in the order of 8.3, and propose the repair or report the defect.

Comments in the paper's review threads come from the requester unless the requester says otherwise. Findings from an independent reviewer, or from any reviewer without delegated authority, are proposals, not requester decisions (1.5). Hold one that conflicts with an active requirement under 1.6 rather than applying it.

8.3 **Repair premises and structure before rewording.** When the requester states the understanding they want, rebuild around it. For a report of confusion, a defect found in steps 1 to 3 of 7.2, or a reconstruction, repair in this order:

1. Reassess which claims and explanation the requested outcome needs, under 1.4 and 2.9. Replan a wrong question, evidence chain, or claim ownership rather than patching wording.
2. Reassess the section map under 2.6.
3. Name each relation the reader cannot see and show it in the form 5.2 gives that relation.
4. Where the reader cannot see why a claim holds, add the missing steps from its premises under 2.5 rather than rewording the claim.
5. Only then cut, reword, or move content, keeping established terms and explicit relationships under 4.1, 4.2, and 4.5.
6. Adjust layout last.

A second report of confusion about the same section, in any words, means the last repair missed what the reader lacks. Before editing again, read every question about that section together and write in the planning records the one idea they circle around: the relation, premise, or distinction the section does not show. Then start again at step 1 and rebuild the section around that idea, reconsidering structure, premises, and interpretation, rather than rewording the element the latest question names. Do not add notes that document the review response.

8.4 **Recheck every active requirement after repair.** After a repair, read the revision against every active requirement gathered under 8.1, including earlier requests the fix affects, and identify where each is satisfied. Repair the same defect in every affected section within the editing scope, report it in sections outside that scope, then check again under Article 7.

Change only what the flagged defect requires, wherever it occurs: keep what the reader needs, and do not swing to the opposite extreme.

8.5 **Reply to a review thread in one line.** A reply to a completed change or a confirmation is one line naming what the comment changed and where. A question gets its answer, its basis in one clause, and where the answer went in. Content moved elsewhere is reported with its destination. A change to the reviewer's own wording, or one the reviewer did not ask for, gets one clause of reason. Do not restate what the notes or the text now say, list moved items or renumbered floats, paste the text before and after, or report a change made for another comment. When the reviewer is right, admit it directly and say what changed or will change. When the reviewer misread, say what the text meant, that its wording invited the misreading, and how it changed. These two take up to three sentences.

## Article 9. Report

9.1 **Report changes, defects, and unchecked scope.** Report changes, remaining defects, and unfinished or unchecked scope. State a confirmed error before explaining its correction. Report content findings separately from production checks, and unrelated defects with the rule and sections checked. For a review of defects across the paper, compile it, read every page with image(), and report counts by defect type. Omit search mechanics and details not needed to assess the change or decide the next step.
